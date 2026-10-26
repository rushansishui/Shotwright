"""配音标注：在文案里标出停顿和强调。

* 停顿：``[停顿]`` 或 ``[pause]``，默认 0.5 秒；也可以写时长，例如 ``[停顿 1.2]``（0.1–3 秒）。
* 强调：用星号包住，例如 ``这台咖啡机*一键*出咖啡``。强调的部分单独合成，语速放慢一点、
  音量提高一点，前面留一个很短的停顿——所有配音服务都适用，不依赖 SSML。

字幕、时长估算和翻译前都用 :func:`plain` 去掉标注。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PAUSE = re.compile(r"\[(?:停顿|pause)(?:\s+(\d+(?:\.\d+)?))?\]", re.IGNORECASE)
EMPHASIS = re.compile(r"\*([^*\n]{1,60})\*")
DEFAULT_PAUSE = 0.5
EMPHASIS_GAP = 0.12
"""强调前的短停顿（秒）。"""
EMPHASIS_RATE = 0.9
EMPHASIS_VOLUME = 1.2


@dataclass
class Segment:
    text: str
    pause_before: float = 0.0
    emphasis: bool = False


def has_marks(text: str) -> bool:
    return bool(PAUSE.search(text) or EMPHASIS.search(text))


def plain(text: str) -> str:
    """去掉停顿和强调标注后的文案（字幕、估算时长用）。"""
    return re.sub(r"[ \t]{2,}", " ", EMPHASIS.sub(r"\1", PAUSE.sub("", text))).strip()


def _pause_value(match: re.Match) -> float:
    value = float(match.group(1)) if match.group(1) else DEFAULT_PAUSE
    return max(0.1, min(3.0, value))


def pause_seconds(text: str) -> float:
    """标注带来的额外静音时长（停顿 + 强调前的短停顿）。"""
    return sum(_pause_value(m) for m in PAUSE.finditer(text)) + EMPHASIS_GAP * len(EMPHASIS.findall(text))


def segments(text: str) -> list[Segment]:
    """按停顿和强调把文案切成几段，每段单独合成；连续的停顿会累加。

    只有标点的片段不单独合成（配音服务读不出来），直接丢掉。
    """
    result: list[Segment] = []
    pending = 0.0
    position = 0
    for match in PAUSE.finditer(text):
        chunk = _split_emphasis(text[position : match.start()], pending)
        result += chunk
        pending = (0.0 if chunk else pending) + _pause_value(match)
        position = match.end()
    result += _split_emphasis(text[position:], pending)
    return result


def _speakable(text: str) -> bool:
    return bool(re.search(r"\w", text))


def _split_emphasis(text: str, pause_before: float) -> list[Segment]:
    parts: list[Segment] = []
    position = 0
    pause = pause_before
    for match in EMPHASIS.finditer(text):
        before = text[position : match.start()]
        if _speakable(before):
            parts.append(Segment(before.strip(), pause))
            pause = 0.0
        parts.append(Segment(match.group(1).strip(), pause + EMPHASIS_GAP, emphasis=True))
        pause = 0.0
        position = match.end()
    rest = text[position:]
    if _speakable(rest):
        parts.append(Segment(rest.strip(), pause))
    return parts
