"""AI 生成标题：根据诉求和文案给出几条候选大标题，并为每个镜头写一句小标题。"""

from __future__ import annotations

import json

from shotwright.core import languages
from shotwright.core.models import VIDEO_TYPE_NAMES, Project
from shotwright.providers.base import LLMProvider, ProviderError
from shotwright.services import speech
from shotwright.services.storyboard import _extract_json

PROMPT = """你是短视频标题编辑。根据视频的诉求和每个镜头的文案，用 {language} 写标题。
- headlines：5 条候选大标题，显示在画面顶部，抓眼球但不夸大、不标题党；中文每条不超过 14 个字，英文不超过 8 个词；风格各不相同（悬念、数字、利益点、情绪、直白）。
- intro_subtitle：片头标题卡的一句副标题，中文不超过 16 个字。
- shots：为每个镜头写一句小标题，概括这个镜头讲什么，中文不超过 8 个字、英文不超过 4 个词；index 是镜头序号（从 1 开始）。
只输出 JSON：{{"headlines": ["..."], "intro_subtitle": "...", "shots": [{{"index": 1, "title": "..."}}]}}"""


def suggest_titles(llm: LLMProvider, project: Project) -> tuple[list[str], str, dict[int, str]]:
    """返回（候选大标题, 片头副标题, {镜头序号: 小标题}）。"""
    if not project.shots:
        raise ProviderError("项目里还没有镜头")
    language = languages.get(project.language).prompt_name
    lines = [f"视频类型：{VIDEO_TYPE_NAMES[project.video_type]}"] if project.video_type else []
    if project.subject.strip():
        lines.append(f"诉求：{project.subject.strip()[:1500]}")
    lines.append("镜头文案：")
    lines += [
        json.dumps({"index": i, "text": speech.plain(shot.text)[:300]}, ensure_ascii=False)
        for i, shot in enumerate(project.shots, start=1)
    ]
    data = _extract_json(
        llm.complete(
            [
                {"role": "system", "content": PROMPT.format(language=language)},
                {"role": "user", "content": "\n".join(lines)},
            ],
            json_mode=True,
        )
    )
    headlines = [str(item).strip()[:80] for item in data.get("headlines") or [] if str(item).strip()][:10]
    if not headlines:
        raise ProviderError("大模型没有给出标题")
    subtitle = str(data.get("intro_subtitle") or "").strip()[:80]
    shots: dict[int, str] = {}
    for item in data.get("shots") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        title = str(item.get("title") or "").strip()[:60]
        if title and 1 <= index <= len(project.shots):
            shots[index] = title
    return headlines, subtitle, shots
