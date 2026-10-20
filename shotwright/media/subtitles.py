"""字幕：切分、按像素宽度换行、生成 ASS / SRT。

每个镜头的文案按标点切成若干条字幕，在该镜头的配音时间段内按字数比例分配时长。
换行按真实字体的像素宽度计算：中日韩文字可以在任意字之间断开，拉丁文字按词断开。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from shotwright.core.models import SubtitleStyle

_BREAK_AFTER = re.compile(r"(?<=[。！？!?；;，,、：:…])|(?<=[.!?])\s+")
_CJK_CHAR = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef\u3000-\u303f]")
_TRAILING_PUNCT = "，,、；;：:。"


@dataclass
class Cue:
    start: float
    end: float
    text: str


@lru_cache(maxsize=16)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def font_family(path: Path) -> str:
    """ASS 用字体族名引用字体，而不是文件名。"""
    return _font(str(path), 32).getname()[0]


def _tokens(text: str) -> list[str]:
    """中日韩逐字成词，拉丁文字按词（保留后面的空格）。"""
    tokens: list[str] = []
    for match in re.finditer(r"[A-Za-z0-9'’\-]+\s*|\s+|.", text):
        tokens.append(match.group(0))
    return tokens


def wrap(text: str, font_path: Path, size: int, max_width: int) -> list[str]:
    """按像素宽度换行，并让各行长度尽量均衡（避免\"一长行 + 一两个字\"）。"""
    lines = _greedy_wrap(text, font_path, size, max_width)
    if len(lines) < 2:
        return lines
    low, high = max_width // 2, max_width
    best = lines
    while high - low > 4:
        middle = (low + high) // 2
        attempt = _greedy_wrap(text, font_path, size, middle)
        if len(attempt) == len(lines):
            best, high = attempt, middle
        else:
            low = middle
    return best


def _greedy_wrap(text: str, font_path: Path, size: int, max_width: int) -> list[str]:
    font = _font(str(font_path), size)
    lines: list[str] = []
    current = ""
    for token in _tokens(text):
        candidate = current + token
        if current and font.getlength(candidate.rstrip()) > max_width:
            lines.append(current.rstrip())
            current = token.lstrip()
        else:
            current = candidate
    if current.strip():
        lines.append(current.rstrip())
    # 避免标点落在行首。
    for i in range(1, len(lines)):
        while lines[i] and lines[i][0] in "，。、！？；：,.!?;:)）」』" and len(lines[i]) > 1:
            lines[i - 1] += lines[i][0]
            lines[i] = lines[i][1:]
    return lines


def _units(text: str) -> int:
    return len(_CJK_CHAR.findall(text)) + len(re.findall(r"[A-Za-z0-9]+", text)) * 2 or 1


def split_cues(
    text: str,
    start: float,
    end: float,
    *,
    font_path: Path,
    size: int,
    max_width: int,
    max_lines: int = 2,
) -> list[Cue]:
    """把一段文案切成若干条字幕，每条最多 ``max_lines`` 行。"""
    phrases = [p.strip() for p in _BREAK_AFTER.split(text.strip()) if p.strip()]
    groups: list[str] = []
    current = ""
    for phrase in phrases:
        joiner = "" if not current or _CJK_CHAR.search(phrase[:1]) or _CJK_CHAR.search(current[-1:]) else " "
        candidate = current + joiner + phrase
        if current and len(wrap(candidate, font_path, size, max_width)) > max_lines:
            groups.append(current)
            current = phrase
        else:
            current = candidate
    if current:
        groups.append(current)

    # 单个短语本身超过两行时再按行切开。
    pieces: list[str] = []
    for group in groups:
        lines = wrap(group, font_path, size, max_width)
        for i in range(0, len(lines), max_lines):
            pieces.append("\n".join(lines[i : i + max_lines]))

    total = sum(_units(p) for p in pieces) or 1
    cues: list[Cue] = []
    cursor = start
    span = max(end - start, 0.1)
    for index, piece in enumerate(pieces):
        length = span * _units(piece) / total
        cue_end = end if index == len(pieces) - 1 else cursor + length
        display = "\n".join(line.rstrip(_TRAILING_PUNCT) or line for line in piece.split("\n"))
        cues.append(Cue(round(cursor, 3), round(cue_end, 3), display))
        cursor = cue_end
    return cues


def _ass_time(seconds: float) -> str:
    centis = int(round(seconds * 100))
    h, rem = divmod(centis, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    h, rem = divmod(millis, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_color(hex_color: str) -> str:
    r, g, b = hex_color[1:3], hex_color[3:5], hex_color[5:7]
    return f"&H00{b}{g}{r}".upper()


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def to_ass(cues: list[Cue], style: SubtitleStyle, *, width: int, height: int, family: str) -> str:
    alignment = {"bottom": 2, "middle": 5, "top": 8}[style.position]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{family},{style.size},{_ass_color(style.color)},&H000000FF,{_ass_color(style.outline_color)},&H64000000,0,0,0,0,100,100,0,0,1,{style.outline:g},0,{alignment},60,60,{style.margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [
        f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,{_ass_escape(c.text)}"
        for c in cues
    ]
    return header + "\n".join(lines) + "\n"


def to_srt(cues: list[Cue]) -> str:
    blocks = [
        f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n" for i, c in enumerate(cues, start=1)
    ]
    return "\n".join(blocks)
