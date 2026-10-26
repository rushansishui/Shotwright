"""用看图模型检查生成的画面：是否切题、主体（产品、人物）是否与参考图一致。

只在配置了看图模型（大模型的 ``vision_model``）时工作；没配置时什么也不做，
不影响生成。检查结果写进镜头的版本记录（得分和问题），同一批多条候选时自动
选中得分最高的一条，用户仍然可以改选。检查不会重新提交收费的生成任务。
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path

from shotwright.config import Settings
from shotwright.core.models import Project, Shot
from shotwright.core.store import ProjectStore
from shotwright.media.ffmpeg import MediaError, extract_frame
from shotwright.providers import registry
from shotwright.providers.base import ProviderError

log = logging.getLogger("shotwright.inspect")

ISSUE_THRESHOLD = 0.6
"""一致性或切题得分低于这个值时记为问题，建议重做。"""
MAX_REFERENCES = 2

PROMPT = """你是视频质检员。第 1 到第 {refs} 张图是参考图（{ref_labels}），之后的 {frames} 张是同一个生成镜头里按时间顺序截取的画面。
这个镜头应当呈现：{description}

请检查：
1. relevance：画面是否符合上面的描述（0–1）；
2. consistency：画面里的产品或人物与参考图是否一致——形状、颜色、材质、Logo 位置、五官和发型（0–1；没有参考图时填 1）；
3. issue：发现的主要问题，用一句中文写清楚（例如「产品颜色变成了黑色」「手指畸形」「出现了乱码文字」），没有问题写空字符串。

只输出 JSON：{{"relevance": 0.0, "consistency": 0.0, "issue": ""}}"""


def vision_llm(settings: Settings):
    """配置了看图模型时返回大模型实例，否则返回 None。"""
    try:
        llm = registry.create("llm", None, settings)
    except (ProviderError, ValueError, KeyError):
        return None
    if not getattr(llm, "vision_model", "") or not hasattr(llm, "see"):
        return None
    return llm


def reference_images(store: ProjectStore, project: Project, shot: Shot) -> list[tuple[str, Path]]:
    """镜头用到的主体参考图：全片设定里关联的参考图优先，其次主体库里的产品和人物图。"""
    from shotwright.services import prompting

    result: list[tuple[str, Path]] = []
    codes = prompting.used_codes(project, shot.visual.prompt)
    for subject in project.look.subjects:
        if subject.reference and (not codes or subject.code in codes):
            asset = project.assets.get(subject.reference)
            if asset is not None and asset.kind == "image":
                result.append((subject.name or subject.code, store.asset_path(project, asset.id)))
    for ref in project.references:
        if not ref.enabled or ref.role not in ("product", "person"):
            continue
        asset = project.assets.get(ref.asset_id)
        if asset is not None and asset.kind == "image":
            path = store.asset_path(project, asset.id)
            if all(path != existing for _label, existing in result):
                result.append((ref.label or ref.role, path))
    return result[:MAX_REFERENCES]


def sample_frames(store: ProjectStore, project: Project, asset_id: str, work: Path) -> list[Path]:
    """视频取 20%、50%、80% 三帧，图片直接缩小。"""
    asset = project.assets[asset_id]
    source = store.asset_path(project, asset_id)
    if asset.kind == "image":
        return [extract_frame(source, work / f"{asset_id}.jpg", at=0.0)]
    duration = asset.duration or 1.0
    return [
        extract_frame(source, work / f"{asset_id}-{index}.jpg", at=duration * fraction)
        for index, fraction in enumerate((0.2, 0.5, 0.8))
    ]


def _parse(reply: str) -> tuple[float, float, str]:
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        raise ProviderError("看图模型没有返回 JSON")
    data = json.loads(match.group(0))

    def score(key: str) -> float:
        try:
            return max(0.0, min(1.0, float(data.get(key, 0))))
        except (TypeError, ValueError):
            return 0.0

    return score("relevance"), score("consistency"), str(data.get("issue") or "").strip()[:200]


def score_asset(
    llm, store: ProjectStore, project: Project, shot: Shot, asset_id: str
) -> tuple[float, str]:
    """检查一个候选素材，返回（综合得分, 问题）。有参考图时一致性占六成，否则只看切题程度。"""
    refs = reference_images(store, project, shot)
    with tempfile.TemporaryDirectory(prefix="shotwright-inspect-") as temp:
        work = Path(temp)
        frames = sample_frames(store, project, asset_id, work)
        ref_frames = [extract_frame(path, work / f"ref-{index}.jpg", at=0.0) for index, (_l, path) in enumerate(refs)]
        description = (shot.visual.prompt or shot.visual.image_prompt or shot.text)[:600]
        prompt = PROMPT.format(
            refs=len(ref_frames),
            ref_labels="、".join(label for label, _path in refs) or "无",
            frames=len(frames),
            description=description,
        )
        relevance, consistency, issue = _parse(llm.see(prompt, [*ref_frames, *frames]))
    total = 0.4 * relevance + 0.6 * consistency if refs else relevance
    if _NO_ISSUE.fullmatch(issue):
        issue = ""
    if not issue and min(relevance, consistency) < ISSUE_THRESHOLD:
        issue = "主体与参考图不一致" if consistency < ISSUE_THRESHOLD else "画面与描述不符"
    return round(total, 3), issue


_NO_ISSUE = re.compile(r"\s*(无|没有|无明显问题|没有问题|未发现问题|none|no issues?)?\s*[。.]?\s*", re.IGNORECASE)


def inspect_takes(
    store: ProjectStore, settings: Settings, project: Project, shot: Shot, asset_ids: list[str]
) -> bool:
    """检查刚生成的候选，把得分和问题写进版本记录；多条时选中得分最高的一条。

    没有配置看图模型时返回 False，什么也不做；单条检查失败只记日志。
    """
    llm = vision_llm(settings)
    if llm is None or not asset_ids:
        return False
    scored: list[tuple[float, str]] = []
    for asset_id in asset_ids:
        take = shot.visual.take(asset_id)
        if take is None or asset_id not in project.assets:
            continue
        try:
            take.score, take.issue = score_asset(llm, store, project, shot, asset_id)
        except (ProviderError, MediaError, ValueError) as exc:
            log.warning("项目 %s 镜头 %s：看图检查失败：%s", project.id, shot.id, exc)
            continue
        scored.append((take.score, asset_id))
        log.info("项目 %s 镜头 %s：候选 %s 得分 %.2f %s", project.id, shot.id, asset_id, take.score, take.issue)
    best = max(scored, default=None)
    if best is not None and len(scored) > 1 and shot.visual.selected in asset_ids:
        shot.visual.selected = best[1]
    return bool(scored)


STOCK_PROMPT = """下面 {n} 张图依次是素材库候选视频的截图（第 1 张对应候选 1，依此类推）。
这个镜头需要的画面：{description}

请逐个评估：relevance 是与需要的画面的切题程度（0–1）；text 表示画面里是否有水印、字幕、文字或明显的商标（true / false）。
只输出 JSON：{{"items": [{{"index": 1, "relevance": 0.0, "text": false}}]}}"""


def rank_stock(
    store: ProjectStore, settings: Settings, project: Project, shot: Shot, asset_ids: list[str]
) -> list[str]:
    """用看图模型给素材库候选排序：切题的排前面，带水印或文字的排最后并标注。

    一次请求看完所有候选（每个取中间一帧）。没有配置看图模型或检查失败时保持原顺序。
    """
    llm = vision_llm(settings)
    if llm is None or len(asset_ids) < 2:
        return asset_ids
    description = "；".join(filter(None, [shot.text[:200], shot.visual.prompt[:300], ", ".join(shot.visual.terms)]))
    try:
        with tempfile.TemporaryDirectory(prefix="shotwright-rank-") as temp:
            work = Path(temp)
            frames = []
            for asset_id in asset_ids:
                asset = project.assets[asset_id]
                source = store.asset_path(project, asset_id)
                at = (asset.duration or 0.0) / 2 if asset.kind == "video" else 0.0
                frames.append(extract_frame(source, work / f"{asset_id}.jpg", at=at))
            reply = llm.see(STOCK_PROMPT.format(n=len(frames), description=description[:600]), frames)
        match = re.search(r"\{.*\}", reply, re.DOTALL)
        items = json.loads(match.group(0)).get("items", []) if match else []
    except (ProviderError, MediaError, ValueError) as exc:
        log.warning("项目 %s 镜头 %s：素材排序失败，保持原顺序：%s", project.id, shot.id, exc)
        return asset_ids
    scores: dict[str, float] = {}
    for item in items:
        try:
            asset_id = asset_ids[int(item.get("index")) - 1]
            relevance = max(0.0, min(1.0, float(item.get("relevance", 0))))
        except (TypeError, ValueError, IndexError):
            continue
        has_text = bool(item.get("text"))
        asset = project.assets[asset_id]
        meta = {**asset.meta, "score": f"{relevance:.2f}"}
        if has_text:
            meta["issue"] = "画面里有水印或文字"
        project.assets[asset_id] = asset.model_copy(update={"meta": meta})
        scores[asset_id] = relevance - (1.0 if has_text else 0.0)
    ordered = sorted(asset_ids, key=lambda asset_id: scores.get(asset_id, 0.0), reverse=True)
    log.info("项目 %s 镜头 %s：素材按看图检查排序 %s", project.id, shot.id, ordered)
    return ordered


FINAL_PROMPT = """下面 {n} 张图是一条成片里按顺序截取的画面，每张来自一个镜头（第 1 张是镜头 {first}，依此类推）。
画面底部或顶部的字幕、价格和卖点文字是后期加上的，不算问题。
请逐张检查这些常见的生成瑕疵：手指或肢体畸形、面部扭曲、物体融合或残缺、画面里出现乱码或错误的文字、明显的水印。
只输出 JSON：{{"items": [{{"index": 1, "issues": ["问题，用一句中文写清楚"]}}]}}；没有问题的画面 issues 为空列表。"""

FRAMES_PER_CALL = 6


def inspect_final(
    settings: Settings, video: Path, starts: list[float], durations: list[float]
) -> list[dict]:
    """成片抽帧检查：每个镜头取中间一帧，交给看图模型找手指、面部、乱码文字等瑕疵。

    返回 [{"shot": 镜头序号, "issues": [...]}]，只包含有问题的镜头。没有配置看图模型时返回空列表。
    """
    llm = vision_llm(settings)
    if llm is None or not starts:
        return []
    found: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="shotwright-final-check-") as temp:
        work = Path(temp)
        frames = [
            extract_frame(video, work / f"shot{index}.jpg", at=start + duration / 2)
            for index, (start, duration) in enumerate(zip(starts, durations, strict=True))
        ]
        for offset in range(0, len(frames), FRAMES_PER_CALL):
            batch = frames[offset : offset + FRAMES_PER_CALL]
            try:
                reply = llm.see(FINAL_PROMPT.format(n=len(batch), first=offset + 1), batch)
                match = re.search(r"\{.*\}", reply, re.DOTALL)
                items = json.loads(match.group(0)).get("items", []) if match else []
            except (ProviderError, ValueError) as exc:
                log.warning("成片抽帧检查失败：%s", exc)
                continue
            for item in items:
                try:
                    shot = offset + int(item.get("index"))
                except (TypeError, ValueError):
                    continue
                issues = [str(text).strip()[:120] for text in item.get("issues") or [] if str(text).strip()]
                issues = [text for text in issues if not _NO_ISSUE.fullmatch(text)]
                if issues and 1 <= shot <= len(frames):
                    found.append({"shot": shot, "issues": issues})
    return found
