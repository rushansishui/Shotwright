"""分镜生成：主题 → 文案 → 镜头列表。

两种方式：

* :func:`split_script` 不联网，把现成文案按句子切成镜头；
* :func:`generate_storyboard` 调用大模型，一次产出标题、每个镜头的文案、
  英文检索词和画面提示词。
"""

from __future__ import annotations

import json
import re

from shotwright.core.models import VIDEO_TYPE_NAMES, Shot, ShotVisual
from shotwright.providers.base import LLMProvider, ProviderError

_SENTENCE_END = re.compile(r"(?<=[。！？!?；;…])\s*|(?<=\.)\s+|\n+")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_THAI = re.compile(r"[\u0e00-\u0e7f]")


def _length(text: str) -> int:
    """中日韩按字计，拼音文字按词计（一个词约 2 个单位），泰文约 3 个字符一个单位。"""
    cjk = len(_CJK.findall(text))
    thai = len(_THAI.findall(text))
    words = len(re.findall(r"[^\W\d_]+|\d+", _THAI.sub(" ", _CJK.sub(" ", text))))
    return cjk + words * 2 + thai // 3


def split_script(script: str, *, max_units: int = 28) -> list[Shot]:
    """按句切分，并把短句合并，让每个镜头大约 3–8 秒。"""
    sentences = [part.strip() for part in _SENTENCE_END.split(script) if part and part.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        joined = f"{current}{'' if _CJK.search(sentence) else ' '}{sentence}".strip() if current else sentence
        if current and _length(joined) > max_units:
            chunks.append(current)
            current = sentence
        else:
            current = joined
    if current:
        chunks.append(current)
    return [Shot(text=chunk, visual=ShotVisual(mode="stock")) for chunk in chunks]


SYSTEM_PROMPT = """你是短视频编导。根据用户给的主题和诉求，写一条短视频的口播文案，并拆成分镜。
要求：
- 用 {language} 写文案，口语化，开头 1–2 句要抓人。
- {length_rule}
- 每个镜头 1–2 句话，朗读 3–8 秒。
- 每个镜头给出 2–3 个英文素材检索词（用于 Pexels 之类的图库，具体、可拍到的画面，例如 "city night traffic"）。
- 每个镜头给出一句英文画面提示词，用于 AI 视频生成（描述主体、动作、镜头运动、光线）。
- 只输出 JSON，不要任何解释，格式：
{{"title": "标题", "shots": [{{"text": "文案", "terms": ["..."], "prompt": "..."}}]}}"""


def _length_rule(shots: int | None, duration: float | None) -> str:
    """把"镜头数"和"总时长"翻译成提示词里的一句话。"""
    if shots and duration:
        return f"一共 {shots} 个镜头，整条视频约 {duration:g} 秒。"
    if shots:
        return f"一共 {shots} 个镜头左右。"
    if duration:
        # 按每个镜头 4–8 秒估算，告诉模型自己决定分几个镜头。
        low = max(1, round(duration / 8))
        high = max(low + 1, round(duration / 4))
        return f"整条视频约 {duration:g} 秒，请据此决定镜头数量（大约 {low}–{high} 个）。"
    return "镜头数量由你按内容决定，整条视频控制在 30–60 秒。"


def _user_message(subject: str, video_type: str | None) -> str:
    kind = VIDEO_TYPE_NAMES.get(video_type or "")
    prefix = f"视频类型：{kind}\n" if kind else ""
    return f"{prefix}主题与诉求：{subject}"


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ProviderError("大模型没有返回 JSON")
    try:
        return json.loads(cleaned[start : end + 1])
    except ValueError as exc:
        raise ProviderError("大模型返回的 JSON 无法解析") from exc


def generate_storyboard(
    llm: LLMProvider,
    subject: str,
    *,
    language: str = "简体中文",
    shots: int | None = None,
    duration: float | None = None,
    video_type: str | None = None,
) -> tuple[str, list[Shot]]:
    """根据诉求生成分镜。``shots`` / ``duration`` 为空时交给大模型自行决定。"""
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(language=language, length_rule=_length_rule(shots, duration)),
        },
        {"role": "user", "content": _user_message(subject, video_type)},
    ]
    data = _extract_json(llm.complete(messages, json_mode=True))
    title = str(data.get("title") or subject)[:120]
    result: list[Shot] = []
    for item in data.get("shots") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        terms = [str(t).strip() for t in item.get("terms") or [] if str(t).strip()][:5]
        result.append(
            Shot(
                text=text[:500],
                visual=ShotVisual(mode="stock", terms=terms, prompt=str(item.get("prompt") or "")[:1000]),
            )
        )
    if not result:
        raise ProviderError("大模型没有返回任何镜头")
    return title, result
