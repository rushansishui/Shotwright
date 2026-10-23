"""镜头画面：检索或生成候选素材，并登记到项目。"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from shotwright.config import Settings
from shotwright.core.fingerprint import fingerprint
from shotwright.core.ids import new_id
from shotwright.core.models import Asset, AssetFolder, PendingTask, Project, Shot, Take, TakeKind
from shotwright.core.store import ProjectStore, read_json, write_json
from shotwright.media.ffmpeg import MediaError, concat_videos, extract_frame, probe
from shotwright.providers import registry
from shotwright.providers.base import (
    BudgetExceeded,
    ChargeConfirmationRequired,
    ImageGenProvider,
    ProviderError,
    ProviderNotConfigured,
    StockCandidate,
    StockProvider,
    VideoGenProvider,
)
from shotwright.services import prompting
from shotwright.services.durations import estimated_shot_seconds

log = logging.getLogger(__name__)

Quality = Literal["draft", "final"]

SEARCH_CACHE_VERSION = "stock-search-1"
"""检索结果缓存的版本；改检索参数的含义时提升它，让旧缓存失效。"""
SEARCH_CACHE_SECONDS = 7 * 24 * 3600
"""检索结果缓存保留 7 天。素材库的列表变动不频繁，太久的缓存意义不大。"""


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


def _search_cache_path(store: ProjectStore, project: Project, key: str) -> Path:
    return store.cache_dir(project.id, "stock-search") / f"{key}.json"


def _cached_candidates(store: ProjectStore, project: Project, key: str) -> list[StockCandidate] | None:
    """读检索结果缓存；过期或格式不对时当没有缓存。"""
    path = _search_cache_path(store, project, key)
    data = read_json(path)
    if not data:
        return None
    if data.get("version") != SEARCH_CACHE_VERSION:
        return None
    try:
        saved_at = float(data.get("saved_at") or 0)
        if time.time() - saved_at > SEARCH_CACHE_SECONDS:
            return None
        return [StockCandidate(**item) for item in data.get("candidates") or []]
    except (TypeError, ValueError):
        return None


def _save_candidates(
    store: ProjectStore, project: Project, key: str, candidates: list[StockCandidate]
) -> None:
    path = _search_cache_path(store, project, key)
    try:
        write_json(
            path,
            {
                "version": SEARCH_CACHE_VERSION,
                "saved_at": time.time(),
                "candidates": [asdict(candidate) for candidate in candidates],
            },
        )
    except OSError as exc:
        # 缓存写不进去不影响检索本身。
        log.warning("写入素材检索缓存失败：%s", exc)


def fetch_stock_candidates(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    *,
    limit: int = 3,
) -> list[str]:
    """按检索词找素材并下载前 ``limit`` 个，返回新增的素材 id。"""
    provider_id = _provider_id(settings, "stock", shot)
    provider: StockProvider = registry.create("stock", provider_id, settings)  # type: ignore[assignment]
    terms = shot.visual.terms or _fallback_terms(project)
    if not terms:
        raise ProviderError("这个镜头没有检索词，请先填写英文检索词")
    min_duration = min(estimated_shot_seconds(project, shot), 10.0)
    found: list[StockCandidate] = []
    for term in terms:
        key = fingerprint(
            SEARCH_CACHE_VERSION, provider_id, term, project.canvas.aspect, round(min_duration, 1), limit
        )
        cached = _cached_candidates(store, project, key)
        if cached is None:
            cached = provider.search(
                term, aspect=project.canvas.aspect, min_duration=min_duration, limit=limit
            )
            _save_candidates(store, project, key, cached)
        else:
            log.info("项目 %s：检索词 %r 命中缓存", project.id, term)
        found.extend(cached)
        if len(found) >= limit:
            break
    seen = {project.assets[a].meta.get("remote_id") for a in shot.visual.candidates if a in project.assets}
    added: list[str] = []
    failed = 0
    last_error = ""
    with tempfile.TemporaryDirectory(prefix="shotwright-") as temp:
        for index, candidate in enumerate(found):
            if candidate.remote_id in seen:
                continue
            try:
                path = _download_with_retry(provider, candidate, Path(temp) / f"c{index}")
                meta = {**candidate.meta, "remote_id": candidate.remote_id, "preview_url": candidate.preview_url}
                asset = _register_media(
                    store, project, path, source=candidate.provider, folder="stock", meta=meta
                )
            except (ProviderError, MediaError) as exc:
                # 单个候选下载失败不该拖垮整批：跳过它，继续下一个。
                failed += 1
                last_error = str(exc)
                log.warning("项目 %s：候选 %s 下载失败，跳过：%s", project.id, candidate.remote_id, exc)
                continue
            added.append(asset.id)
            if len(added) >= limit:
                break
    if failed:
        log.warning(
            "项目 %s：%d 个候选素材下载失败，已跳过（%s）", project.id, failed, last_error
        )
    if not added and not shot.visual.candidates:
        if failed:
            raise ProviderError(
                f"素材下载失败（{last_error}）。素材库可能正在限流，请稍等几秒再试"
            )
        raise ProviderError("没有找到合适的素材，试试换几个检索词")
    if added and failed:
        # 有成功也有失败时，在镜头上记一条提示，让用户知道候选不全。
        shot.error = f"{failed} 个候选下载失败，已跳过"[:200]
    _attach(shot, added)
    return added


def _download_with_retry(
    provider: StockProvider, candidate: StockCandidate, output: Path, *, attempts: int = 2
) -> Path:
    """下载单个候选素材。连接被远端掐断时短暂退避后重试一次。

    素材库的 CDN 在短时间内收到大量请求时会直接断开连接（RemoteProtocolError），
    等几百毫秒往往就恢复了，比让整批失败体验好得多。
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return provider.download(candidate, output)
        except ProviderError as exc:
            last = exc
            message = str(exc)
            # 只有网络类错误值得重试；HTTP 4xx、文件过大等重试也没用。
            retryable = any(
                token in message for token in ("RemoteProtocolError", "ConnectError", "ReadTimeout", "Timeout")
            )
            if not retryable or attempt == attempts - 1:
                raise
            time.sleep(0.6 * (attempt + 1))
    raise last or ProviderError("素材下载失败")


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
MAX_TAKES_PER_BATCH = 3
"""一次点击最多生成几条 AI 视频 / 几张关键帧，避免误操作花太多钱。"""
DRAFT_RESOLUTION = "720p"
"""草稿档 AI 视频的默认分辨率；可在 Provider 配置里用 draft_resolution 修改。"""


def check_budget(
    settings: Settings,
    project: Project,
    shot: Shot,
    kind: str,
    provider_id: str | None,
    *,
    resolution: str = "",
    seconds: float | None = None,
) -> None:
    """项目设了费用上限时，估算本次生成的参考费用，累计会超过上限就拒绝提交。"""
    if project.budget is None:
        return
    from shotwright.services import cost

    amount = cost.generation_cost(
        settings, project, shot, kind, provider_id or "", resolution=resolution, seconds=seconds
    )
    if amount > 0 and project.cost_credits + amount > project.budget + 1e-9:
        raise BudgetExceeded(
            f"超出项目费用上限：已花约 {project.cost_credits:.2f} 元，本次约 {amount:.2f} 元，"
            f"上限 {project.budget:.2f} 元。可以在渲染面板提高上限"
        )


def apply_quality(settings: Settings, provider: object, provider_id: str | None, quality: Quality) -> str:
    """按草稿 / 成片档设置生成分辨率，返回实际使用的分辨率（Provider 不支持时返回空字符串）。

    成片档用配置里的分辨率；草稿档用 ``draft_resolution``（默认 720p），比成片档低时才降。
    """
    current = getattr(provider, "resolution", None)
    if not isinstance(current, str):
        return ""
    if quality != "draft":
        return current
    config = settings.provider_settings("video_gen", provider_id or "")
    draft = (config.get("draft_resolution") or DRAFT_RESOLUTION).strip().lower()
    order = ("480p", "720p", "1080p")
    if draft in order and current in order and order.index(draft) < order.index(current):
        provider.resolution = draft  # type: ignore[attr-defined]
        return draft
    return current


def generate_keyframes(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    count: int,
    *,
    confirm_charge: bool = False,
) -> list[str]:
    """给 AI 视频镜头先出几张关键帧图片（同一批）。选中一张作首帧后再生成视频，更可控也更省钱。"""
    batch = new_id()
    added: list[str] = []
    for _ in range(max(1, min(count, MAX_TAKES_PER_BATCH))):
        added += generate_image_candidate(
            store, settings, project, shot, confirm_charge=confirm_charge, keyframe=True, batch=batch
        )
    return added
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


def reference_assets(project: Project, *, roles: tuple[str, ...] | None = None) -> list:
    """项目里启用的参考素材（按角色过滤）。"""
    result = []
    for ref in project.references:
        if not ref.enabled:
            continue
        if roles is not None and ref.role not in roles:
            continue
        asset = project.assets.get(ref.asset_id)
        if asset is None or asset.kind != "image":
            continue
        result.append((ref, asset))
    return result


def _spec(kind: str, provider_id: str | None):
    return next((s for s in registry.specs(kind) if s.id == provider_id), None)  # type: ignore[arg-type]


def build_prompt(settings: Settings, project: Project, shot: Shot, kind: str, provider_id: str | None) -> str:
    """交给生成模型的完整提示词：全片设定、运镜、衔接拼好，再按模型支持的语言准备。"""
    target = "image" if kind == "image_gen" else "video"
    text = prompting.compose(project, shot, target=target)
    spec = _spec(kind, provider_id)
    languages = spec.prompt_languages if spec is not None else ("en",)
    return prompting.for_provider(settings, project, text, languages)


def _chained_first_frame(store: ProjectStore, project: Project, shot: Shot) -> tuple[Path | None, str]:
    """上一个镜头选中的是 AI 视频时，取它的最后一帧登记成图片素材，作为这个镜头的首帧。"""
    index = next((i for i, item in enumerate(project.shots) if item.id == shot.id), 0)
    if index == 0:
        return None, ""
    previous = project.shots[index - 1]
    asset = project.assets.get(previous.visual.selected or "")
    if asset is None or asset.kind != "video" or asset_folder(asset) != "ai-video":
        return None, ""
    try:
        with tempfile.TemporaryDirectory(prefix="shotwright-chain-") as temp:
            frame = extract_frame(store.asset_path(project, asset.id), Path(temp) / "last.jpg", width=0)
            image = _register_media(
                store, project, frame, source="ffmpeg", folder="ai-image", meta={"last_frame_of": asset.id}
            )
    except (MediaError, ProviderError) as exc:
        log.warning("项目 %s：取上一镜头的最后一帧失败，改用其他首帧：%s", project.id, exc)
        return None, ""
    return store.asset_path(project, image.id), image.id


def _reference_first_frame(store: ProjectStore, project: Project) -> tuple[Path | None, str]:
    """没有显式首帧时，用第一张产品/人物参考图作为首帧，保证商品外观不走样。"""
    for _ref, asset in reference_assets(project, roles=("product", "person")):
        return store.asset_path(project, asset.id), asset.id
    return None, ""


def generate_ai_candidate(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    *,
    confirm_charge: bool = False,
    edit_selected: bool = False,
    quality: Quality = "final",
    batch: str = "",
) -> list[str]:
    """AI 视频。已选中的画面是图片时，把它作为首帧（图生视频）。

    ``edit_selected`` 为真时是「改画面」：把当前选中的视频作为参考视频交给支持
    视频转视频的模型，按提示词改写。只有用户明确点了「改画面」才会这样做，普通的
    「生成」永远是文生 / 图生视频，避免悄悄换成另一种计费方式。

    拿到远端任务 id 后立刻写进项目，进程重启后还能继续轮询，避免重复扣费。
    """
    if not shot.visual.prompt.strip():
        raise ProviderError("这个镜头没有画面提示词")
    provider_id = _provider_id(settings, "video_gen", shot)
    # 先实例化（只检查配置，不发请求），未配置时报"缺少配置"而不是"需要确认"。
    provider: VideoGenProvider = registry.create("video_gen", provider_id, settings)  # type: ignore[assignment]
    resolution = apply_quality(settings, provider, provider_id, quality)
    reference_video: Path | None = None
    if edit_selected:
        reference_video = _edit_source(store, project, shot, provider_id)
    require_charge_confirmation(settings, "video_gen", provider_id, confirm_charge)
    first_frame = None if reference_video is not None else _first_frame(store, project, shot)
    frame_source = shot.visual.first_frame or ""
    if first_frame is None and reference_video is None and project.chain_frames:
        # 首尾帧衔接：上一个 AI 镜头的最后一帧作为首帧，两个镜头在交界处画面连续。
        first_frame, frame_source = _chained_first_frame(store, project, shot)
    if first_frame is None and reference_video is None:
        # 没有显式首帧时，优先用产品/人物参考图，保证商品外观不走样。
        first_frame, frame_source = _reference_first_frame(store, project)
    prompt = build_prompt(settings, project, shot, "video_gen", provider_id)
    meta = {"prompt": prompt[:1500]}
    if resolution:
        meta["resolution"] = resolution
    if first_frame is not None:
        meta["first_frame"] = frame_source
    if reference_video is not None:
        meta["edited_from"] = shot.visual.selected or ""

    def remember_task(task_id: str) -> None:
        """任务 id 一拿到就落盘。这里直接用 store 的锁与最新项目，避免覆盖并发编辑。"""
        with store.lock(project.id):
            latest = store.load(project.id)
            try:
                target = latest.shot(shot.id)
            except KeyError:
                return
            target.visual.pending = PendingTask(
                provider=provider_id or "video_gen", task_id=task_id, kind="video_gen"
            )
            store.save(latest)
            project.revision = latest.revision

    wanted = estimated_shot_seconds(project, shot)
    longest = _max_duration(provider, provider_id)
    extend = reference_video is None and longest is not None and wanted > longest + 0.5
    check_budget(settings, project, shot, "video_gen", provider_id, resolution=resolution)
    kind: TakeKind = "edit" if reference_video is not None else "extend" if extend else "video"
    with tempfile.TemporaryDirectory(prefix="shotwright-") as temp:
        work = Path(temp)
        if not extend:
            path = provider.generate(
                prompt,
                aspect=project.canvas.aspect,
                duration=wanted,
                output=work / "gen",
                first_frame=first_frame,
                reference_video=reference_video,
                on_task=remember_task,
            )
            spent = [wanted]
        else:
            # 末帧续生成：配音比模型最长时长还长时，分段生成，每段从上一段的最后一帧接着生成，
            # 最后拼成一段。替代慢放、定格和循环；每段单独计费。
            path, spent = _generate_extended(
                provider, prompt, project, work, wanted, longest or wanted, first_frame, remember_task
            )
            meta["segments"] = str(len(spent))
        asset = _register_media(
            store, project, path, source=provider_id or "video_gen", folder="ai-video", meta=meta
        )
    shot.visual.pending = None
    for seconds in spent:
        record_spend(
            store, settings, project, shot, "video_gen", provider_id or "", resolution=resolution, seconds=seconds
        )
    _attach(shot, [asset.id], select=True, take=kind, batch=batch)
    return [asset.id]


MAX_EXTEND_PARTS = 4
"""末帧续生成最多分几段，避免配音特别长时花太多钱；剩下的时长仍按补足方式处理。"""


def _max_duration(provider: object, provider_id: str | None) -> float | None:
    """生成服务一段最长能生成多少秒：优先用 Provider 实例的配置，其次用声明的上限。"""
    configured = getattr(provider, "max_duration", None)
    if isinstance(configured, int | float) and configured > 0:
        return float(configured)
    spec = _spec("video_gen", provider_id)
    return spec.max_duration if spec is not None else None


def _generate_extended(
    provider: VideoGenProvider,
    prompt: str,
    project: Project,
    work: Path,
    wanted: float,
    longest: float,
    first_frame: Path | None,
    remember_task,
) -> tuple[Path, list[float]]:
    lang = prompting.prompt_language(project)
    segments: list[Path] = []
    spent: list[float] = []
    remaining = wanted
    frame = first_frame
    while remaining > 0.5 and len(segments) < MAX_EXTEND_PARTS:
        part = len(segments)
        length = min(remaining, longest)
        text = prompt if part == 0 else f"{prompting.CONTINUE[lang]}\n{prompt}"
        path = provider.generate(
            text,
            aspect=project.canvas.aspect,
            duration=length,
            output=work / f"part{part}",
            first_frame=frame,
            on_task=remember_task,
        )
        segments.append(path)
        spent.append(length)
        try:
            actual = probe(path).duration or length
        except MediaError:
            actual = length
        remaining -= actual
        if remaining > 0.5 and len(segments) < MAX_EXTEND_PARTS:
            frame = extract_frame(path, work / f"part{part}-last.jpg", width=0)
    if len(segments) == 1:
        return segments[0], spent
    return concat_videos(segments, work / "extended.mp4", fps=project.canvas.fps), spent


def _edit_source(store: ProjectStore, project: Project, shot: Shot, provider_id: str | None) -> Path:
    """「改画面」的参考视频：当前选中的视频素材，且生成服务必须支持视频转视频。"""
    spec = next((s for s in registry.specs("video_gen") if s.id == provider_id), None)
    if spec is None or not spec.video_to_video:
        raise ProviderError("当前的 AI 视频服务不支持改画面")
    asset = project.assets.get(shot.visual.selected or "")
    if asset is None or asset.kind != "video":
        raise ProviderError("改画面需要先选中一段视频")
    return store.asset_path(project, asset.id)


def generate_image_candidate(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    *,
    confirm_charge: bool = False,
    keyframe: bool = False,
    batch: str = "",
) -> list[str]:
    """AI 图片：渲染时配合慢推镜头；也可以再作为 AI 视频的首帧。

    ``keyframe`` 为真时是给 AI 视频镜头出关键帧：结果放进候选供选作首帧，不改变当前选中的画面。
    """
    if not (shot.visual.image_prompt.strip() or shot.visual.prompt.strip()):
        raise ProviderError("这个镜头没有画面提示词")
    provider_id = _provider_id(settings, "image_gen", shot)
    provider: ImageGenProvider = registry.create("image_gen", provider_id, settings)  # type: ignore[assignment]
    require_charge_confirmation(settings, "image_gen", provider_id, confirm_charge)
    check_budget(settings, project, shot, "image_gen", provider_id)
    prompt = build_prompt(settings, project, shot, "image_gen", provider_id)
    with tempfile.TemporaryDirectory(prefix="shotwright-") as temp:
        path = provider.generate(prompt, aspect=project.canvas.aspect, output=Path(temp) / "gen")
        asset = _register_media(
            store,
            project,
            path,
            source=provider_id or "image_gen",
            folder="ai-image",
            meta={"prompt": prompt[:1500]},
        )
    record_spend(store, settings, project, shot, "image_gen", provider_id or "")
    _attach(shot, [asset.id], select=not keyframe, take="keyframe" if keyframe else "image", batch=batch)
    return [asset.id]


def refresh_candidates(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    *,
    confirm_charge: bool = False,
    edit_selected: bool = False,
    count: int = 1,
    keyframes: int = 0,
    quality: Quality = "final",
) -> list[str]:
    """检索或生成候选素材。

    * ``count``：AI 视频一次生成几条（同一批，1–3）；
    * ``keyframes``：AI 视频镜头先出几张关键帧图片（2–3），选中一张作首帧后再生成视频；
    * ``quality``：草稿档用较低分辨率生成 AI 视频，更快更省钱。
    """
    index = next((i for i, s in enumerate(project.shots) if s.id == shot.id), -1) + 1
    log.info(
        "项目 %s 镜头 %d：开始%s（%s）",
        project.id,
        index,
        "改画面" if edit_selected else _ACTIONS.get(shot.visual.mode, "处理"),
        shot.visual.mode,
    )
    try:
        if edit_selected:
            added = generate_ai_candidate(
                store, settings, project, shot, confirm_charge=confirm_charge, edit_selected=True, quality=quality
            )
        elif keyframes and shot.visual.mode == "ai":
            added = generate_keyframes(store, settings, project, shot, keyframes, confirm_charge=confirm_charge)
        else:
            added = _refresh(
                store, settings, project, shot, confirm_charge=confirm_charge, count=count, quality=quality
            )
        if shot.visual.mode in ("ai", "image") or edit_selected:
            from shotwright.services import inspect

            inspect.inspect_takes(store, settings, project, shot, added)
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
    count: int = 1,
    quality: Quality = "final",
) -> list[str]:
    if shot.visual.mode == "stock":
        return fetch_stock_candidates(store, settings, project, shot)
    if shot.visual.mode == "ai":
        batch = new_id()
        added: list[str] = []
        for _ in range(max(1, min(count, MAX_TAKES_PER_BATCH))):
            added += generate_ai_candidate(
                store, settings, project, shot, confirm_charge=confirm_charge, quality=quality, batch=batch
            )
        return added
    if shot.visual.mode == "image":
        return generate_image_candidate(store, settings, project, shot, confirm_charge=confirm_charge)
    raise ProviderError("这个镜头的画面来源不需要检索或生成")


def resume_pending(store: ProjectStore, settings: Settings, project: Project, shot: Shot) -> list[str]:
    """继续轮询一个已提交但没取回结果的 AI 视频任务（重启后恢复用）。

    成功时把结果登记为素材并清掉 pending；失败时也清掉，让用户可以重新提交。
    """
    pending = shot.visual.pending
    if pending is None:
        return []
    if pending.kind != "video_gen":
        shot.visual.pending = None
        return []
    provider_id = pending.provider
    provider: VideoGenProvider = registry.create("video_gen", provider_id, settings)  # type: ignore[assignment]
    if not hasattr(provider, "resume"):
        shot.visual.pending = None
        return []
    meta = {"prompt": shot.visual.prompt[:500], "resumed_from": pending.task_id}
    with tempfile.TemporaryDirectory(prefix="shotwright-") as temp:
        path = provider.resume(pending.task_id, output=Path(temp) / "gen")
        asset = _register_media(
            store, project, path, source=provider_id, folder="ai-video", meta=meta
        )
    shot.visual.pending = None
    record_spend(store, settings, project, shot, "video_gen", provider_id)
    _attach(shot, [asset.id], select=True, take="video")
    return [asset.id]


def record_spend(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    kind: str,
    provider_id: str,
    *,
    resolution: str = "",
    seconds: float | None = None,
) -> None:
    """收费生成成功后，把参考费用累加到项目（直接写盘，调用方之后的合并不会覆盖它）。"""
    from shotwright.services import cost

    amount = cost.generation_cost(
        settings, project, shot, kind, provider_id, resolution=resolution, seconds=seconds
    )
    if amount <= 0:
        return
    with store.lock(project.id):
        latest = store.load(project.id)
        latest.cost_credits = round(latest.cost_credits + amount, 4)
        store.save(latest)
        project.cost_credits = latest.cost_credits
        project.revision = latest.revision
    log.info("项目 %s：本次生成参考费用 %.2f 元，累计 %.2f 元", project.id, amount, project.cost_credits)


def _attach(
    shot: Shot,
    asset_ids: list[str],
    *,
    select: bool = False,
    take: TakeKind | None = None,
    batch: str = "",
) -> None:
    """把素材加进候选。``take`` 不为空时同时记一条生成版本（同一批共用 ``batch``）。"""
    candidates = list(shot.visual.candidates)
    for asset_id in asset_ids:
        if asset_id not in candidates:
            candidates.append(asset_id)
    shot.visual.candidates = candidates[-MAX_CANDIDATES:]
    takes = [item for item in shot.visual.takes if item.asset_id in shot.visual.candidates]
    if take is not None:
        known = {item.asset_id for item in takes}
        prompt = shot.visual.image_prompt or shot.visual.prompt if take in ("image", "keyframe") else shot.visual.prompt
        takes += [
            Take(asset_id=asset_id, kind=take, batch=batch or new_id(), prompt=prompt)
            for asset_id in asset_ids
            if asset_id not in known and asset_id in shot.visual.candidates
        ]
    shot.visual.takes = takes
    if take == "keyframe":
        # 关键帧只是首帧的备选，不改变当前选中的画面。
        return
    if asset_ids and (
        select or shot.visual.selected is None or shot.visual.selected not in shot.visual.candidates
    ):
        shot.visual.selected = asset_ids[0]


def add_upload(
    store: ProjectStore, project: Project, path: Path, *, filename: str, prefer_audio: bool = False
) -> Asset:
    """登记用户上传的文件（画面或音频）。

    ``prefer_audio`` 用于背景音乐：有些音乐文件装在视频容器里（带封面的 mp4/m4a、
    MV 片段），按画面登记会变成视频素材，背景音乐那边就选不了。这类文件按要求
    直接当音频登记，渲染时本来也只取它的音轨。
    """
    try:
        info = probe(path)
    except MediaError as exc:
        raise ProviderError("无法识别上传的文件，请上传常见的视频、图片或音频格式") from exc
    suffix = Path(filename).suffix.lower()
    audio_suffixes = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
    if prefer_audio:
        if not info.has_audio:
            raise ProviderError("这个文件里没有音轨，无法作为背景音乐")
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
    if info.has_video and suffix not in audio_suffixes:
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
    """按来源推断素材应放的子目录，用于整理旧项目。"""
    if asset.source == "upload":
        return "upload"
    ids = {kind: {spec.id for spec in registry.specs(kind)} for kind in ("stock", "video_gen", "image_gen", "music")}
    if asset.source in ids["stock"]:
        return "stock"
    if asset.kind == "video" and (asset.source in ids["video_gen"] or asset.source == "video_gen"):
        return "ai-video"
    if asset.kind == "image" and (asset.source in ids["image_gen"] or asset.source == "image_gen"):
        return "ai-image"
    # 旧项目里已检索的背景音乐可能落在 other/，按来源或类型归到 music/。
    if asset.kind == "audio" and (asset.source in ids["music"] or asset.source == "music"):
        return "music"
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
