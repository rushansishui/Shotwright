"""背景音乐：把镜头画面提炼成检索词，从音乐 Provider 搜候选并入库。

检索词由大模型从视频诉求和镜头提示词里提炼（例如 "calm cinematic ambient"）；
大模型不可用时退回用视频诉求的前半段。
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from shotwright.config import Settings
from shotwright.core.models import Asset, Project
from shotwright.core.store import ProjectStore
from shotwright.media.ffmpeg import MediaError, probe
from shotwright.providers import registry
from shotwright.providers.base import LLMProvider, MusicCandidate, MusicProvider, ProviderError

log = logging.getLogger(__name__)

MAX_CANDIDATES = 5
MIN_DURATION = 20.0
MAX_DURATION = 600.0

_SEARCH_PROMPT = """你是短视频配乐师。根据视频的主题和画面描述，给出 2–4 个英文音乐检索词。
要求：
- 描述音乐的**风格、情绪、配器**，不要描述画面内容，例如 "calm cinematic ambient piano"。
- 用空格分隔关键词，不要标点，不要品牌或人名。
- 只输出 JSON：{{"queries": ["...", "..."]}}"""


def build_default_query(project: Project) -> str:
    """大模型不可用时的兜底检索词：用视频诉求的开头部分。"""
    subject = project.subject.strip()
    if subject:
        return subject[:60]
    return "calm background music"


def suggest_queries(llm: LLMProvider, project: Project) -> list[str]:
    """让大模型把诉求和镜头提示词提炼成音乐检索词；失败时退回默认词。"""
    prompts = [shot.visual.prompt.strip() for shot in project.shots if shot.visual.prompt.strip()]
    payload = {
        "subject": project.subject.strip(),
        "visuals": prompts[:8],
    }
    messages = [
        {"role": "system", "content": _SEARCH_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        from shotwright.services.storyboard import _extract_json

        data = _extract_json(llm.complete(messages, json_mode=True))
    except (ProviderError, ValueError):
        return [build_default_query(project)]
    queries = [str(item).strip()[:80] for item in data.get("queries") or [] if str(item).strip()]
    return queries[:4] or [build_default_query(project)]


def _register_audio(
    store: ProjectStore, project: Project, path: Path, *, source: str, meta: dict[str, str]
) -> Asset:
    try:
        info = probe(path)
    except MediaError as exc:
        raise ProviderError(f"背景音乐无法解析：{exc}") from exc
    if not info.has_audio:
        raise ProviderError("检索到的文件没有声音")
    return store.add_asset(
        project,
        path,
        kind="audio",
        source=source,
        suffix=".mp3",
        duration=info.duration,
        meta=meta,
        move=True,
        folder="music",
    )


def search_bgm(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    *,
    query: str | None = None,
    limit: int = MAX_CANDIDATES,
) -> tuple[list[str], str]:
    """检索背景音乐并登记到项目。返回 (新增素材 id, 实际使用的检索词)。"""
    provider_id = settings.default_provider("music")
    provider: MusicProvider = registry.create("music", provider_id, settings)  # type: ignore[assignment]

    chosen_query = (query or "").strip()
    if not chosen_query:
        try:
            llm = registry.create("llm", None, settings)
            queries = suggest_queries(llm, project)  # type: ignore[arg-type]
        except ProviderError:
            queries = [build_default_query(project)]
    else:
        queries = [chosen_query]

    target_duration = project.target_seconds or 60.0
    min_duration = min(MIN_DURATION, target_duration)
    max_duration = max(MAX_DURATION, target_duration)

    added: list[str] = []
    seen: set[str] = {asset_id for asset_id in project.bgm.candidates if asset_id in project.assets}
    used_query = queries[0]
    with tempfile.TemporaryDirectory(prefix="shotwright-bgm-") as temp:
        for index, term in enumerate(queries):
            if len(added) >= limit:
                break
            found = provider.search(term, min_duration=min_duration, max_duration=max_duration, limit=limit)
            for position, candidate in enumerate(found):
                if len(added) >= limit:
                    break
                # 同一首曲子可能被多个检索词命中，按远端 id 跳过已经收录的。
                if candidate.remote_id in seen:
                    continue
                path = provider.download(candidate, Path(temp) / f"bgm{index}-{position}")
                asset = _register_audio(
                    store, project, path, source=candidate.provider, meta=_meta(candidate, term)
                )
                seen.add(candidate.remote_id)
                if asset.id not in added:
                    added.append(asset.id)
                    if len(added) == 1:
                        # 记下真正搜到第一首的检索词，界面上回填它。
                        used_query = term

    if not added:
        raise ProviderError("没有找到合适的背景音乐，试试换个检索词")
    log.info("项目 %s：检索到 %d 首背景音乐（检索词 %s）", project.id, len(added), used_query)
    return added, used_query


def _meta(candidate: MusicCandidate, query: str) -> dict[str, str]:
    meta = {
        "title": candidate.title,
        "author": candidate.author,
        "license": candidate.meta.get("license", ""),
        "source_url": candidate.meta.get("source_url", ""),
        "query": query,
    }
    return {key: value for key, value in meta.items() if value}
