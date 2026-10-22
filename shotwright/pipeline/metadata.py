"""渲染后写入 MP4 元数据，标识 AI 生成内容。

写入以下字段：
- title       : 项目标题
- comment     : "Made with Shotwright (AI-generated content)"
- description : 含 BGM 授权信息的简介（CC BY 曲目会列出署名要求）
- software    : Shotwright/<version>
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from shotwright import __version__
from shotwright.core.models import Project
from shotwright.media.ffmpeg import LOCAL_PROTOCOLS, MediaError, run_ffmpeg

log = logging.getLogger(__name__)

AI_LABEL = "Made with Shotwright (AI-generated content)"


def _bgm_credit(project: Project) -> str:
    """生成背景音乐的署名文本；CC BY / BY-SA 曲目需要署名。"""
    asset = project.assets.get(project.bgm.asset_id) if project.bgm.asset_id else None
    if asset is None:
        return ""
    kind = asset.meta.get("license_kind", "")
    if kind not in ("cc-by", "cc-by-sa"):
        return ""
    title = asset.meta.get("title", "")
    author = asset.meta.get("author", "")
    license_text = asset.meta.get("license", kind.upper())
    source = asset.meta.get("source_url", "")
    parts = ["BGM:"]
    if title:
        parts.append(f'"{title}"')
    if author:
        parts.append(f"by {author}")
    parts.append(f"({license_text})")
    if source:
        parts.append(source)
    return " ".join(parts)


def tag_video(project: Project, video_path: Path) -> None:
    """为已生成的 MP4 写入 AI 内容标识元数据，原地替换文件。

    写入失败只记录警告，不中断渲染流程。
    """
    description_parts = [AI_LABEL]
    credit = _bgm_credit(project)
    if credit:
        description_parts.append(credit)
    description = " | ".join(description_parts)

    suffix = video_path.suffix
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=video_path.parent, suffix=suffix, delete=False
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
        run_ffmpeg(
            [
                "-protocol_whitelist", LOCAL_PROTOCOLS,
                "-i", str(video_path),
                "-map", "0",
                "-c", "copy",
                "-metadata", f"title={project.title or 'Shotwright'}",
                "-metadata", f"comment={AI_LABEL}",
                "-metadata", f"description={description}",
                "-metadata", f"software=Shotwright/{__version__}",
                "-movflags", "+faststart",
                "-f", "mp4",
                str(tmp_path),
            ]
        )
        tmp_path.replace(video_path)
    except (MediaError, OSError) as exc:
        log.warning("写入 MP4 元数据失败（%s）：%s", video_path.name, exc)
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
