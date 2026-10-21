"""镜头画面：检索或生成候选素材，并登记到项目。"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from shotwright.config import Settings
from shotwright.core.models import Asset, AssetFolder, Project, Shot
from shotwright.core.store import ProjectStore
from shotwright.media.ffmpeg import MediaError, probe
from shotwright.providers import registry
from shotwright.providers.base import (
    ChargeConfirmationRequired,
    ImageGenProvider,
    ProviderError,
    ProviderNotConfigured,
    StockProvider,
    VideoGenProvider,
)
from shotwright.services.durations import estimated_shot_seconds

log = logging.getLogger(__name__)


def _register_media(
    store: ProjectStore,
    project: Project,
    path: Path,
    *,
    source: str,
    folder: AssetFolder,
    meta: dict[str, str] | None = None,
) -> Asset:
    try:
        info = probe(path)
    except MediaError as exc:
        raise ProviderError(f"素材无法解析：{exc}") from exc
    if not info.has_video:
        raise ProviderError("素材不包含画面")
    kind = "image" if not info.duration or info.duration < 0.05 else "video"
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        kind = "image"
    return store.add_asset(
        project,
        path,
        kind=kind,
        source=source,
        width=info.width,
        height=info.height,
        duration=info.duration if kind == "video" else None,
        meta=meta,
        move=True,
        folder=folder,
    )


def _fallback_terms(project: Project) -> list[str]:
    """镜头没填检索词时的兜底：只有视频诉求足够短才拿来当检索词。

    视频诉求可以是 4096 字节的整段说明，直接当检索词基本搜不到东西，还会浪费请求。
    """
    subject = project.subject.strip()
    if not subject or len(subject.encode("utf-8")) > MAX_FALLBACK_TERM_BYTES:
        return []
    return [subject]


def fetch_stock_candidates(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    *,
    limit: int = 3,
) -> list[str]:
    """按检索词找素材并下载前 ``limit`` 个，返回新增的素材 id。"""
    provider: StockProvider = registry.create("stock", _provider_id(settings, "stock", shot), settings)  # type: ignore[assignment]
    terms = shot.visual.terms or _fallback_terms(project)
    if not terms:
        raise ProviderError("这个镜头没有检索词，请先填写英文检索词")
    min_duration = min(estimated_shot_seconds(project, shot), 10.0)
    found = []
    for term in terms:
        found.extend(
            provider.search(term, aspect=project.canvas.aspect, min_duration=min_duration, limit=limit)
        )
        if len(found) >= limit:
            break
    seen = {project.assets[a].meta.get("remote_id") for a in shot.visual.candidates if a in project.assets}
    added: list[str] = []
    with tempfile.TemporaryDirectory(prefix="shotwright-") as temp:
        for index, candidate in enumerate(found):
            if candidate.remote_id in seen:
                continue
            path = provider.download(candidate, Path(temp) / f"c{index}")
            meta = {**candidate.meta, "remote_id": candidate.remote_id, "preview_url": candidate.preview_url}
            asset = _register_media(
                store, project, path, source=candidate.provider, folder="stock", meta=meta
            )
            added.append(asset.id)
            if len(added) >= limit:
                break
    if not added and not shot.visual.candidates:
        raise ProviderError("没有找到合适的素材，试试换几个检索词")
    _attach(shot, added)
    return added


def _provider_id(settings: Settings, kind: str, shot: Shot) -> str | None:
    """镜头记录的 Provider 属于这个类别时才使用，否则用配置里的默认值。

    切换画面来源后 ``visual.provider`` 可能还是上一个类别的（例如 pixabay）。
    """
    chosen = shot.visual.provider
    if chosen:
        try:
            registry.spec(kind, chosen)
            return chosen
        except ProviderError:
            pass
    return settings.default_provider(kind)


def require_charge_confirmation(
    settings: Settings, kind: str, provider_id: str | None, confirmed: bool
) -> None:
    """收费 Provider 没有得到确认时抛 :class:`ChargeConfirmationRequired`。"""
    chosen = provider_id or settings.default_provider(kind)
    if not chosen:
        raise ProviderNotConfigured(f"没有配置 {kind} 类别的 Provider")
    spec = registry.spec(kind, chosen)
    if spec.paid and not confirmed:
        raise ChargeConfirmationRequired(f"{spec.name} 按次收费，需要确认后才会提交")


MAX_CANDIDATES = 30
MAX_FALLBACK_TERM_BYTES = 80
"""视频诉求短于这个长度才拿它当兜底检索词（见 :func:`_fallback_terms`）。"""
_MODE_KINDS = (("stock", "stock"), ("ai", "video_gen"), ("image", "image_gen"))


def asset_mode(asset: Asset) -> str | None:
    """素材来自哪种画面来源：stock / ai / image / upload；无法判断时返回 None。"""
    if asset.source == "upload":
        return "upload"
    for mode, kind in _MODE_KINDS:
        try:
            registry.spec(kind, asset.source)
        except ProviderError:
            continue
        # video_gen 只产出视频、image_gen 只产出图片，用类型排除同名的不同服务。
        if (mode == "ai" and asset.kind != "video") or (mode == "image" and asset.kind != "image"):
            continue
        return mode
    return None


def candidates_for_mode(project: Project, shot: Shot, mode: str) -> list[str]:
    return [
        asset_id
        for asset_id in shot.visual.candidates
        if asset_id in project.assets and asset_mode(project.assets[asset_id]) == mode
    ]


def switch_mode(project: Project, shot: Shot, mode: str) -> None:
    """切换画面来源：选中该来源最近的候选素材；没有时清空，渲染时再自动检索或生成。

    从图片切到 AI 视频时，把原来选中的图片作为首帧，方便"先生成图片、再图生视频"。
    纯色模式保留原选择，切回来时不丢。
    """
    visual = shot.visual
    if mode == visual.mode:
        return
    current = project.assets.get(visual.selected) if visual.selected else None
    if mode == "ai" and current is not None and current.kind == "image" and visual.first_frame is None:
        visual.first_frame = current.id
    visual.mode = mode  # type: ignore[assignment]
    if mode == "color":
        return
    if current is not None and asset_mode(current) == mode:
        return
    matching = candidates_for_mode(project, shot, mode)
    visual.selected = matching[-1] if matching else None


def _first_frame(store: ProjectStore, project: Project, shot: Shot) -> Path | None:
    first = shot.visual.first_frame
    asset = project.assets.get(first) if first else None
    if asset is None or asset.kind != "image":
        return None
    return store.asset_path(project, asset.id)


def generate_ai_candidate(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    *,
    confirm_charge: bool = False,
) -> list[str]:
    """AI 视频。已选中的画面是图片时，把它作为首帧（图生视频）。"""
    if not shot.visual.prompt.strip():
        raise ProviderError("这个镜头没有画面提示词")
    provider_id = _provider_id(settings, "video_gen", shot)
    # 先实例化（只检查配置，不发请求），未配置时报"缺少配置"而不是"需要确认"。
    provider: VideoGenProvider = registry.create("video_gen", provider_id, settings)  # type: ignore[assignment]
    require_charge_confirmation(settings, "video_gen", provider_id, confirm_charge)
    first_frame = _first_frame(store, project, shot)
    meta = {"prompt": shot.visual.prompt[:500]}
    if first_frame is not None:
        meta["first_frame"] = shot.visual.first_frame or ""
    with tempfile.TemporaryDirectory(prefix="shotwright-") as temp:
        path = provider.generate(
            shot.visual.prompt,
            aspect=project.canvas.aspect,
            duration=estimated_shot_seconds(project, shot),
            output=Path(temp) / "gen",
            first_frame=first_frame,
        )
        asset = _register_media(
            store, project, path, source=provider_id or "video_gen", folder="ai-video", meta=meta
        )
    _attach(shot, [asset.id], select=True)
    return [asset.id]


def generate_image_candidate(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    *,
    confirm_charge: bool = False,
) -> list[str]:
    """AI 图片：渲染时配合慢推镜头；也可以再作为 AI 视频的首帧。"""
    if not shot.visual.prompt.strip():
        raise ProviderError("这个镜头没有画面提示词")
    provider_id = _provider_id(settings, "image_gen", shot)
    provider: ImageGenProvider = registry.create("image_gen", provider_id, settings)  # type: ignore[assignment]
    require_charge_confirmation(settings, "image_gen", provider_id, confirm_charge)
    with tempfile.TemporaryDirectory(prefix="shotwright-") as temp:
        path = provider.generate(shot.visual.prompt, aspect=project.canvas.aspect, output=Path(temp) / "gen")
        asset = _register_media(
            store,
            project,
            path,
            source=provider_id or "image_gen",
            folder="ai-image",
            meta={"prompt": shot.visual.prompt[:500]},
        )
    _attach(shot, [asset.id], select=True)
    return [asset.id]


def refresh_candidates(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    *,
    confirm_charge: bool = False,
) -> list[str]:
    index = next((i for i, s in enumerate(project.shots) if s.id == shot.id), -1) + 1
    log.info(
        "项目 %s 镜头 %d：开始%s（%s）",
        project.id,
        index,
        _ACTIONS.get(shot.visual.mode, "处理"),
        shot.visual.mode,
    )
    try:
        added = _refresh(store, settings, project, shot, confirm_charge=confirm_charge)
    except ProviderError as exc:
        log.warning(
            "项目 %s 镜头 %d：%s失败：%s", project.id, index, _ACTIONS.get(shot.visual.mode, "处理"), exc
        )
        raise
    log.info("项目 %s 镜头 %d：新增素材 %s", project.id, index, ", ".join(added) or "无")
    return added


_ACTIONS = {"stock": "检索素材", "ai": "生成 AI 视频", "image": "生成 AI 图片"}


def _refresh(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    *,
    confirm_charge: bool,
) -> list[str]:
    if shot.visual.mode == "stock":
        return fetch_stock_candidates(store, settings, project, shot)
    if shot.visual.mode == "ai":
        return generate_ai_candidate(store, settings, project, shot, confirm_charge=confirm_charge)
    if shot.visual.mode == "image":
        return generate_image_candidate(store, settings, project, shot, confirm_charge=confirm_charge)
    raise ProviderError("这个镜头的画面来源不需要检索或生成")


def _attach(shot: Shot, asset_ids: list[str], *, select: bool = False) -> None:
    candidates = list(shot.visual.candidates)
    for asset_id in asset_ids:
        if asset_id not in candidates:
            candidates.append(asset_id)
    shot.visual.candidates = candidates[-MAX_CANDIDATES:]
    if asset_ids and (
        select or shot.visual.selected is None or shot.visual.selected not in shot.visual.candidates
    ):
        shot.visual.selected = asset_ids[0]


def add_upload(store: ProjectStore, project: Project, path: Path, *, filename: str) -> Asset:
    """登记用户上传的文件（画面或音频）。"""
    try:
        info = probe(path)
    except MediaError as exc:
        raise ProviderError("无法识别上传的文件，请上传常见的视频、图片或音频格式") from exc
    suffix = Path(filename).suffix.lower()
    if info.has_video and suffix not in {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}:
        return _register_media(
            store, project, path, source="upload", folder="upload", meta={"filename": filename[:200]}
        )
    if info.has_audio:
        return store.add_asset(
            project,
            path,
            kind="audio",
            source="upload",
            suffix=suffix or ".mp3",
            duration=info.duration,
            meta={"filename": filename[:200]},
            move=True,
            folder="upload",
        )
    raise ProviderError("上传的文件既没有画面也没有声音")


# ---- 旧项目素材整理 ---------------------------------------------------------


def asset_folder(asset: Asset) -> AssetFolder:
    """按来源推断素材应放的子目录，用于整理 v0.3 之前的项目。"""
    if asset.source == "upload":
        return "upload"
    ids = {kind: {spec.id for spec in registry.specs(kind)} for kind in ("stock", "video_gen", "image_gen")}
    if asset.source in ids["stock"]:
        return "stock"
    if asset.kind == "video" and (asset.source in ids["video_gen"] or asset.source == "video_gen"):
        return "ai-video"
    if asset.kind == "image" and (asset.source in ids["image_gen"] or asset.source == "image_gen"):
        return "ai-image"
    return "other"


def organize_all(store: ProjectStore) -> int:
    """启动时把所有旧项目的素材移到按来源划分的子目录，返回整理了几个项目。"""
    count = 0
    for project in store.list():
        if all(asset.folder for asset in project.assets.values()):
            continue
        try:
            if store.organize_assets(project, asset_folder):
                count += 1
        except OSError as exc:
            log.warning("整理项目 %s 的素材失败：%s", project.id, exc)
    if count:
        log.info("已把 %d 个旧项目的素材按来源分到子目录", count)
    return count
