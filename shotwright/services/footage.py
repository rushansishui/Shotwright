"""原片导入：把一段手机拍的视频按画面变化切成镜头。

流程：
1. 原片登记为普通上传素材；
2. ``ffmpeg`` 的 scene 检测给出切镜点；
3. 生成或替换镜头：每个镜头都引用这段原片，用入出点（``trim_start`` / ``trim_end``）
   选出自己那一段，时长设为片段时长，原声默认保留。

不再把每段切成单独的文件：入出点可以随时微调，拆分、合并镜头也只是改入出点。

切得太多（例如手持抖动被误判）时，按最大镜头数合并相邻段。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from shotwright.config import Settings
from shotwright.core.models import Project, Shot, ShotVisual
from shotwright.core.store import ProjectStore
from shotwright.media.ffmpeg import MediaError, detect_scenes, probe
from shotwright.services.visuals import _register_media

log = logging.getLogger(__name__)

MAX_SHOTS = 30
"""一段原片最多切成多少个镜头，避免抖动把镜头切碎。"""

MIN_SEGMENT = 0.8
"""短于这个秒数的片段并入下一段。"""


@dataclass
class ImportResult:
    source_asset: str
    """原片登记后的素材 id。"""
    shot_ids: list[str]
    segments: int


def _merge_short_starts(starts: list[float], total: float) -> list[float]:
    """把过短的片段并入后一段，并保证不发散。"""
    kept: list[float] = []
    for start in starts:
        if not kept or start - kept[-1] >= MIN_SEGMENT:
            kept.append(start)
    # 最后一段也要够长，否则退回到上一个切点。
    while len(kept) > 1 and total - kept[-1] < MIN_SEGMENT:
        kept.pop()
    return kept


def _limit_shots(starts: list[float], total: float, limit: int) -> list[float]:
    """切得太多时等间隔合并，保留首尾。"""
    if len(starts) <= limit:
        return starts
    step = total / limit
    return [round(step * i, 3) for i in range(limit)]


def import_footage(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    path: Path,
    *,
    filename: str,
    threshold: float = 0.35,
    max_shots: int = MAX_SHOTS,
    replace_shots: bool = False,
) -> ImportResult:
    """导入一段原片并切成镜头。

    ``replace_shots`` 为真时替换项目里现有的镜头，否则追加到末尾。
    """
    try:
        info = probe(path)
    except MediaError as exc:
        raise MediaError(f"无法识别原片：{exc}") from exc
    if not info.has_video:
        raise MediaError("原片里没有画面")
    total = info.duration or 0.0
    if total < MIN_SEGMENT:
        raise MediaError("原片太短，至少要 1 秒")

    # 1. 原片本身登记为素材，方便日后重新切。
    source = _register_media(
        store, project, path, source="upload", folder="upload", meta={"filename": filename[:200], "footage": "1"}
    )

    # 2. 检测切镜点。
    source_path = store.asset_path(project, source.id)
    try:
        starts = detect_scenes(source_path, threshold=threshold)
    except MediaError as exc:
        log.warning("原片 %s 切镜检测失败，按单镜头处理：%s", filename, exc)
        starts = [0.0]
    starts = _merge_short_starts(starts, total)
    starts = _limit_shots(starts, total, max_shots)

    # 3. 每段一个镜头，引用原片的一段。
    shot_ids: list[str] = []
    bounds = [*starts, total]
    for index in range(len(starts)):
        begin, end = round(bounds[index], 3), round(bounds[index + 1], 3)
        shot = Shot(
            text="",
            duration=max(0.1, round(end - begin, 3)),
            visual=ShotVisual(
                mode="upload",
                candidates=[source.id],
                selected=source.id,
                edit_asset=source.id,
                trim_start=begin,
                trim_end=end,
                source_volume=1.0 if info.has_audio else 0.0,
            ),
        )
        shot_ids.append(shot.id)
        project.shots.append(shot)

    if replace_shots:
        # 新镜头已经在末尾，去掉原来那些。
        project.shots = [s for s in project.shots if s.id in shot_ids]
    log.info("原片 %s：切成 %d 个镜头", filename, len(shot_ids))
    return ImportResult(source_asset=source.id, shot_ids=shot_ids, segments=len(shot_ids))
