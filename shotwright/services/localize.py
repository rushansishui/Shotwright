"""多语种：切换项目语言、把一个视频翻译成其他语言版本。

语言版本是独立的项目（自己的配音、字幕、渲染缓存），和原项目共用一个 ``group``，
镜头 id、画面素材、锁定状态都保持一致，只替换文案、配音声音和字幕字体。
素材文件用硬链接复制，不额外占用磁盘（跨磁盘时退回普通复制）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time

from shotwright.core import languages
from shotwright.core.ids import new_id
from shotwright.core.models import Project, VoiceSettings
from shotwright.core.store import ProjectStore
from shotwright.providers.base import LLMProvider, ProviderError
from shotwright.services.storyboard import _extract_json

log = logging.getLogger(__name__)

_BUNDLED_FONTS = {languages.SC_FONT, languages.KR_FONT, languages.THAI_FONT}


def _voice_for(voice: VoiceSettings, code: str) -> VoiceSettings:
    """Edge 配音换成目标语言的默认声音；其他配音服务保持不变。"""
    if voice.provider != "edge" or languages.voice_language(voice.voice) == code:
        return voice
    return voice.model_copy(update={"voice": languages.get(code).default_voice})


def apply_language(project: Project, code: str) -> None:
    """把项目切到另一种语言：更新配音声音和字幕字体（不改文案）。"""
    lang = languages.get(code)
    project.language = lang.code
    project.voice = _voice_for(project.voice, lang.code)
    for shot in project.shots:
        if shot.voice is not None:
            shot.voice = _voice_for(shot.voice, lang.code)
    if project.subtitle.font in _BUNDLED_FONTS:
        # 只替换内置字体；用户自己放进去的字体保持不变（缺字时渲染会自动换用）。
        project.subtitle = project.subtitle.model_copy(update={"font": lang.font})


TRANSLATE_PROMPT = """你是短视频本地化编辑。把用户给的短视频口播文案翻译成 {target}。
要求：
- 口语化、适合配音朗读，符合 {target} 母语者的说法，不要逐字硬译。
- 每个镜头的长度和原文接近（朗读时长相差不超过 20%），镜头数量和顺序不变。
- 品牌、车型、人名等专有名词按 {target} 地区的通用写法；没有通用译名时保留原文。
- 标题也一起翻译。
- 只输出 JSON，不要任何解释，格式和输入相同：
{{"title": "...", "shots": [{{"id": "...", "text": "..."}}]}}"""


def translate_texts(llm: LLMProvider, project: Project, target: str) -> tuple[str, dict[str, str]]:
    """调用大模型翻译标题和所有镜头文案，返回 (标题, {镜头 id: 译文})。"""
    lang = languages.get(target)
    payload = {
        "title": project.title,
        "shots": [{"id": shot.id, "text": shot.text} for shot in project.shots if shot.text.strip()],
    }
    messages = [
        {"role": "system", "content": TRANSLATE_PROMPT.format(target=lang.prompt_name)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    data = _extract_json(llm.complete(messages, json_mode=True))
    texts: dict[str, str] = {}
    for item in data.get("shots") or []:
        if isinstance(item, dict) and item.get("id") and str(item.get("text") or "").strip():
            texts[str(item["id"])] = str(item["text"]).strip()[:2000]
    expected = {item["id"] for item in payload["shots"]}
    missing = expected - set(texts)
    if missing:
        raise ProviderError(f"大模型漏翻了 {len(missing)} 个镜头，请重试")
    title = str(data.get("title") or project.title).strip()[:120]
    return title, texts


def _copy_file(source: str, target: str) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def create_language_version(
    store: ProjectStore,
    llm: LLMProvider,
    project: Project,
    target: str,
) -> Project:
    """生成另一种语言的版本（新项目），返回新项目。"""
    code = languages.normalize(target)
    if code == languages.normalize(project.language):
        raise ProviderError("目标语言和当前项目相同")
    group = project.group or project.id
    for other in store.list():
        if (other.group or other.id) == group and languages.normalize(other.language) == code:
            raise ProviderError(f"已经有{languages.get(code).name_zh}版本了：{other.title}")
    title, texts = translate_texts(llm, project, code)

    version = project.model_copy(deep=True)
    version.id = new_id()
    version.group = group
    version.title = title
    version.revision = 0
    for shot in version.shots:
        if shot.id in texts:
            shot.text = texts[shot.id]
        shot.status, shot.error = "draft", None
    apply_language(version, code)
    version.created_at = version.updated_at = time.time()

    store.create(version)
    source_dir = store.assets_dir(project.id)
    target_dir = store.assets_dir(version.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    for asset in version.assets.values():
        src = source_dir / asset.filename
        if src.is_file() and not (target_dir / asset.filename).exists():
            _copy_file(str(src), str(target_dir / asset.filename))
    log.info("项目 %s 生成了 %s 版本：%s", project.id, code, version.id)
    return version


def versions(store: ProjectStore, project: Project) -> list[Project]:
    group = project.group or project.id
    found = [p for p in store.list() if (p.group or p.id) == group]
    return sorted(found, key=lambda p: (p.id != group, p.created_at))
