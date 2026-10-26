"""字幕：切分、按像素宽度换行、生成 ASS / SRT。

每个镜头的文案按标点切成若干条字幕，在该镜头的配音时间段内按字数比例分配时长。
换行按真实字体的像素宽度计算：中日韩文字可以在任意字之间断开，拉丁文字按词断开。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from shotwright.core.models import SubtitleStyle

_BREAK_AFTER = re.compile(r"(?<=[。！？!?；;，,、：:…])|(?<=[.!?])\s+|\s*\n\s*")
"""短语分界：句读标点之后、英文句末的空格、文案里的换行（换行由字幕自己按宽度重排）。"""
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


_SPACED_WORD = re.compile(
    r"[^\W\d_\u0e00-\u0e7f\u3040-\u30ff\u3400-\u9fff]+(?:['’\-][^\W\d_]+)*\s*|\d+[\d.,]*\s*|\s+|.", re.UNICODE
)


def _tokens(text: str) -> list[str]:
    """可以断行的最小单位：中日韩、泰文逐字；拉丁、西里尔等按词（带后面的空格）。

    组合符号（泰文元音和声调、越南语附加符号）始终跟在前一个字后面，不会被拆到下一行。
    """
    tokens: list[str] = []
    for match in _SPACED_WORD.finditer(text):
        token = match.group(0)
        if tokens and unicodedata.category(token[0]) in ("Mn", "Mc", "Me"):
            tokens[-1] += token
        else:
            tokens.append(token)
    return tokens


@lru_cache(maxsize=16)
def _codepoints(path: str) -> frozenset[int]:
    from fontTools.ttLib import TTFont

    with TTFont(path, lazy=True) as font:
        return frozenset(font.getBestCmap() or {})


def missing_glyphs(font_path: Path, text: str) -> str:
    """字体里没有的字符（去重，忽略空白和控制字符）。"""
    cmap = _codepoints(str(font_path))
    seen: dict[str, None] = {}
    for char in text:
        if char.isspace() or unicodedata.category(char).startswith("C"):
            continue
        if ord(char) not in cmap:
            seen[char] = None
    return "".join(seen)


def pick_font(preferred: Path, fonts_dir: Path, text: str) -> tuple[Path, str]:
    """优先用指定字体；缺字时换成目录里能完整显示的字体。返回 (字体, 仍然缺的字)。"""
    missing = missing_glyphs(preferred, text)
    if not missing:
        return preferred, ""
    best, best_missing = preferred, missing
    # 先试通用黑体（Noto Sans），避免缺字时换成书法、卡通这类风格强烈的字体。
    candidates = sorted(fonts_dir.glob("*.[ot]tf"), key=lambda p: (not p.name.startswith("NotoSans"), p.name))
    for candidate in candidates:
        if candidate == preferred:
            continue
        left = missing_glyphs(candidate, text)
        if len(left) < len(best_missing):
            best, best_missing = candidate, left
            if not left:
                break
    return best, best_missing


def wrap(text: str, font_path: Path, size: int, max_width: int, spacing: float = 0.0) -> list[str]:
    """按像素宽度换行，并让各行长度尽量均衡（避免\"一长行 + 一两个字\"）。``spacing`` 是字间距。"""
    lines = _greedy_wrap(text, font_path, size, max_width, spacing)
    if len(lines) < 2:
        return lines
    low, high = max_width // 2, max_width
    best = lines
    while high - low > 4:
        middle = (low + high) // 2
        attempt = _greedy_wrap(text, font_path, size, middle, spacing)
        if len(attempt) == len(lines):
            best, high = attempt, middle
        else:
            low = middle
    return best


def text_width(text: str, font_path: Path, size: int, spacing: float = 0.0) -> float:
    return _font(str(font_path), size).getlength(text) + spacing * len(text)


def _greedy_wrap(text: str, font_path: Path, size: int, max_width: int, spacing: float = 0.0) -> list[str]:
    lines: list[str] = []
    current = ""
    for token in _tokens(text):
        candidate = current + token
        if current and text_width(candidate.rstrip(), font_path, size, spacing) > max_width:
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
    """朗读时长的粗略权重：中日韩每字 1，拼音文字每词 2，泰文约 3 个字符 1。"""
    cjk = len(_CJK_CHAR.findall(text))
    thai = len(re.findall(r"[\u0e00-\u0e7f]", text))
    words = len(re.findall(r"[^\W\d_\u0e00-\u0e7f]+|\d+", _CJK_CHAR.sub(" ", text)))
    return cjk + words * 2 + thai // 3 or 1


def _word_spans(text: str, words: list[tuple[str, float, float]]) -> list[tuple[int, int, float, float]]:
    """把逐词时间戳对到原文的字符区间；对不上的词跳过，不影响其它词。"""
    spans: list[tuple[int, int, float, float]] = []
    cursor = 0
    for word_text, word_start, word_end in words:
        token = word_text.strip()
        if not token:
            continue
        index = text.find(token, cursor)
        if index < 0:
            continue
        spans.append((index, index + len(token), word_start, word_end))
        cursor = index + len(token)
    return spans


def _span_time(
    spans: list[tuple[int, int, float, float]], char_start: int, char_end: int
) -> tuple[float, float] | None:
    """某个字符区间对应的起止时间；区间里没有完整词时用中点最近的词兜底。"""
    if not spans:
        return None
    inside = [span for span in spans if span[0] >= char_start and span[1] <= char_end]
    if inside:
        return inside[0][2], inside[-1][3]
    middle = (char_start + char_end) / 2
    nearest = min(spans, key=lambda span: abs((span[0] + span[1]) / 2 - middle))
    return nearest[2], nearest[3]


def split_cues(
    text: str,
    start: float,
    end: float,
    *,
    font_path: Path,
    size: int,
    max_width: int,
    max_lines: int = 2,
    words: list[tuple[str, float, float]] | None = None,
    spacing: float = 0.0,
) -> list[Cue]:
    """把一段文案切成若干条字幕，每条最多 ``max_lines`` 行。

    给了 ``words``（配音服务的逐词时间戳，相对本镜头音频开头）时按词对齐；
    否则按字数比例在 ``[start, end]`` 内分配。
    """
    source = text.strip()
    phrases: list[tuple[str, int, int]] = []
    search_from = 0
    for part in _BREAK_AFTER.split(source):
        phrase = part.strip()
        if not phrase:
            continue
        index = source.find(phrase, search_from)
        if index < 0:
            index = search_from
        phrases.append((phrase, index, index + len(phrase)))
        search_from = index + len(phrase)

    groups: list[tuple[str, int, int]] = []
    current = ""
    current_start = current_end = 0
    for phrase, phrase_start, phrase_end in phrases:
        joiner = "" if not current or _CJK_CHAR.search(phrase[:1]) or _CJK_CHAR.search(current[-1:]) else " "
        candidate = current + joiner + phrase
        if current and len(wrap(candidate, font_path, size, max_width, spacing)) > max_lines:
            groups.append((current, current_start, current_end))
            current, current_start, current_end = phrase, phrase_start, phrase_end
        else:
            if not current:
                current_start = phrase_start
            current = candidate
            current_end = phrase_end
    if current:
        groups.append((current, current_start, current_end))

    # 单个短语本身超过两行时再按行切开；字符区间按各段长度比例分配。
    pieces: list[tuple[str, int, int]] = []
    for group, group_start, group_end in groups:
        lines = wrap(group, font_path, size, max_width, spacing)
        if len(lines) <= max_lines:
            # 换行要写进字幕本身：ASS 用的是 WrapStyle 2（不自动换行），不写的话两行会挤成一行超出画面。
            pieces.append(("\n".join(lines), group_start, group_end))
            continue
        total = sum(len(line) for line in lines) or 1
        cursor = group_start
        for offset in range(0, len(lines), max_lines):
            chunk = lines[offset : offset + max_lines]
            span = sum(len(line) for line in chunk)
            piece_end = min(group_end, cursor + round((group_end - group_start) * span / total))
            pieces.append(("\n".join(chunk), cursor, max(0, piece_end)))
            cursor = piece_end

    spans = _word_spans(source, words) if words else []
    total_units = sum(_units(piece) for piece, _, _ in pieces) or 1
    cues: list[Cue] = []
    cursor = start
    span = max(end - start, 0.1)
    for index, (piece, char_start, char_end) in enumerate(pieces):
        if spans:
            timing = _span_time(spans, char_start, char_end)
            cue_start = start + timing[0] if timing else cursor
            cue_end = start + timing[1] if timing else cursor
            # 词之间有空隙时，字幕保持到下一个词开始，避免闪烁。
            if index + 1 < len(pieces):
                following = _span_time(spans, pieces[index + 1][1], pieces[index + 1][2])
                if following:
                    cue_end = start + following[0]
            cue_start = max(start, min(cue_start, end))
            cue_end = max(cue_end, cue_start + 0.1)
            cue_end = min(end, cue_end)
        else:
            length = span * _units(piece) / total_units
            cue_start = cursor
            cue_end = end if index == len(pieces) - 1 else cursor + length
            cursor = cue_end
        display = "\n".join(line.rstrip(_TRAILING_PUNCT) or line for line in piece.split("\n"))
        cues.append(Cue(round(cue_start, 3), round(cue_end, 3), display))
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


def to_srt(cues: list[Cue]) -> str:
    blocks = [
        f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n" for i, c in enumerate(cues, start=1)
    ]
    return "\n".join(blocks)




# ---------------------------------------------------------------------------
# ASS 样式与入场效果（字幕和标题共用）


def _alpha(opacity: float) -> str:
    """ASS 的透明度：00 不透明，FF 全透明。"""
    return f"{round((1 - max(0.0, min(1.0, opacity))) * 255):02X}"


def _ass_rgba(hex_color: str, opacity: float = 1.0) -> str:
    return f"&H{_alpha(opacity)}{_ass_color(hex_color)[4:]}"


_ALIGN_ROW = {"bottom": 0, "middle": 3, "top": 6}
_ALIGN_COL = {"left": 1, "center": 2, "right": 3}


def alignment(position: str, align: str = "center") -> int:
    """ASS 的 \\an 对齐编号（小键盘布局：1–3 底部，4–6 中间，7–9 顶部）。"""
    return _ALIGN_ROW.get(position, 0) + _ALIGN_COL.get(align, 2)


def box_padding(style, size: int) -> int:
    """底框比文字四周多出的距离。"""
    return max(6, round(size * 0.18))


def box_padding_y(size: int) -> int:
    """底框上下的额外留白。字体自带的行高已经在字的上下留了空，再加的话两行的框会重叠出一条深色线。"""
    return 0


def ass_style(name: str, family: str, size: int, style, align_code: int, margin_l: int, margin_r: int, margin_v: int) -> str:
    """一条 ASS Style。``style`` 是 :class:`TextStyle`（或带同名字段的对象）。

    开底框时用 BorderStyle 3：底框颜色占用描边颜色，描边宽度变成底框内边距。
    卡拉 OK 时主色是高亮色、次色是原色（还没读到的字显示次色）。
    """
    opacity = style.opacity
    boxed = style.background_opacity > 0
    karaoke = style.animation == "karaoke"
    primary = _ass_rgba(style.highlight_color if karaoke else style.color, opacity)
    secondary = _ass_rgba(style.color, opacity) if karaoke else "&HFF000000"
    if boxed:
        border_style, outline_colour = 3, _ass_rgba(style.background, style.background_opacity * opacity)
        outline = box_padding(style, size)
    else:
        border_style, outline_colour = 1, _ass_rgba(style.outline_color, opacity)
        outline = style.outline
    back = _ass_rgba(style.shadow_color, style.shadow_opacity * opacity)
    flag = lambda value: -1 if value else 0  # noqa: E731
    return (
        f"Style: {name},{family},{size},{primary},{secondary},{outline_colour},{back},"
        f"{flag(style.bold)},{flag(style.italic)},{flag(style.underline)},0,100,100,{style.spacing:g},0,"
        f"{border_style},{outline:g},{style.shadow:g},{align_code},{margin_l},{margin_r},{margin_v},1"
    )


def anchor(align_code: int, width: int, height: int, margin_l: int, margin_r: int, margin_v: int) -> tuple[int, int]:
    """对齐点的像素坐标（\\pos / \\move 用）。"""
    column = (align_code - 1) % 3
    row = (align_code - 1) // 3
    x = (margin_l, width // 2, width - margin_r)[column]
    y = (height - margin_v, height // 2, margin_v)[row]
    return x, y


@dataclass(frozen=True)
class Keywords:
    """关键词高亮：这些词换成 ``color`` 并按 ``scale`` 放大（只用于字幕）。"""

    words: tuple[str, ...]
    color: str
    scale: float = 1.15


_ASCII_WORD = re.compile(r"[A-Za-z0-9]")


def keyword_spans(text: str, words) -> list[tuple[int, int]]:
    """关键词在文字里的位置（不重叠，长词优先，英文不区分大小写、按整词匹配）。"""
    if not words:
        return []
    lower = text.lower()
    taken = [False] * len(text)
    spans: list[tuple[int, int]] = []
    for word in sorted({w.strip() for w in words if w.strip()}, key=len, reverse=True):
        needle = word.lower()
        latin = bool(_ASCII_WORD.match(needle[0])) or bool(_ASCII_WORD.match(needle[-1]))
        start = 0
        while (i := lower.find(needle, start)) >= 0:
            j = i + len(needle)
            start = i + 1
            if any(taken[i:j]) or "\n" in text[i:j]:
                continue
            if latin and ((i > 0 and _ASCII_WORD.match(text[i - 1])) or (j < len(text) and _ASCII_WORD.match(text[j]))):
                continue
            spans.append((i, j))
            for k in range(i, j):
                taken[k] = True
    return sorted(spans)


def _bgr(hex_color: str) -> str:
    return f"&H{hex_color[5:7]}{hex_color[3:5]}{hex_color[1:3]}&".upper()


class _Marker:
    """给关键词加上颜色和字号标签，结束后恢复成样式本来的颜色和字号。"""

    def __init__(self, text: str, style, size: int, keywords: Keywords | None):
        self.spans = keyword_spans(text, keywords.words) if keywords else []
        if keywords and self.spans:
            base = style.highlight_color if style.animation == "karaoke" else style.color
            self.open = f"{{\\1c{_bgr(keywords.color)}\\fs{round(size * keywords.scale)}}}"
            self.close = f"{{\\1c{_bgr(base)}\\fs{size}}}"

    def marked(self, a: int) -> bool:
        return any(x <= a < y for x, y in self.spans)

    def plain(self, text: str, offset: int = 0) -> str:
        """``text`` 是全文从 ``offset`` 开始的一段。"""
        if not self.spans:
            return _ass_escape(text)
        out: list[str] = []
        pos = 0
        for x, y in self.spans:
            a, b = max(x - offset, 0), min(y - offset, len(text))
            if a >= b or b <= pos:
                continue
            out.append(_ass_escape(text[pos:a]) + self.open + _ass_escape(text[a:b]) + self.close)
            pos = b
        out.append(_ass_escape(text[pos:]))
        return "".join(out)

    def token(self, token: str, at: int) -> str:
        escaped = _ass_escape(token)
        return self.open + escaped + self.close if self.spans and self.marked(at) else escaped


def _line_ranges(text: str) -> list[tuple[int, int]]:
    ranges, start = [], 0
    for line in text.split("\n"):
        ranges.append((start, start + len(line)))
        start += len(line) + 1
    return ranges


def _reveal_plan(text: str, total: float) -> list[list[tuple[int, str, int]]]:
    """逐字效果的时间分配：每行一组（字在全文中的位置, 字, 百分之一秒）。按字数（拉丁文按词）平分 ``total``。"""
    lines: list[list[tuple[int, str, int]]] = []
    pieces: list[tuple[int, int, str, int]] = []
    for row, (start, end) in enumerate(_line_ranges(text)):
        at = start
        for token in _tokens(text[start:end]):
            pieces.append((row, at, token, _units(token) if token.strip() else 0))
            at += len(token)
        lines.append([])
    units_left = sum(weight for *_, weight in pieces) or 1
    centis_left = max(1, round(total * 100))
    for row, at, token, weight in pieces:
        centis = round(centis_left * weight / units_left) if units_left else 0
        centis_left -= centis
        units_left -= weight
        lines[row].append((at, token, centis))
    return lines


def _reveal_tags(text: str, tag: str, total: float, *, line: int | None = None, marker: _Marker | None = None) -> str:
    """逐字效果：每个字（拉丁文每个词）前面加 ``\\k`` 类标签，总时长 ``total`` 秒。

    ``line`` 给出时只输出这一行，前面用一段空的 ``\\k`` 等到前几行显示完。
    """
    plan = _reveal_plan(text, total)
    marker = marker or _Marker(text, None, 0, None)
    rows = range(len(plan)) if line is None else [line]
    out: list[str] = []
    if line is not None:
        delay = sum(centis for row in plan[:line] for *_, centis in row)
        if delay:
            out.append(f"{{\\{tag}{delay}}}")
    for index, row in enumerate(rows):
        if index:
            out.append("\\N")
        for at, token, centis in plan[row]:
            out.append(f"{{\\{tag}{centis}}}{marker.token(token, at)}")
    return "".join(out)


LOOP_PERIOD = {"pulse": 1200, "swing": 1600}
"""循环效果一个周期的毫秒数。"""
LOOP_START = 400
MAX_LOOPS = 300


def _loop_tags(loop: str, begin: int, end: int) -> str:
    """循环效果：从 ``begin`` 到 ``end``（毫秒，相对事件开始）一直重复，最后回到原样。"""
    period = LOOP_PERIOD.get(loop)
    if not period or end - begin < period:
        return ""
    count = min((end - begin) // period, MAX_LOOPS)
    half = period // 2
    out: list[str] = []
    for n in range(count):
        t = begin + n * period
        if loop == "pulse":
            out.append(f"\\t({t},{t + half},\\fscx107\\fscy107)\\t({t + half},{t + period},\\fscx100\\fscy100)")
        else:
            quarter = period // 4
            out.append(
                f"\\t({t},{t + quarter},\\frz3)\\t({t + quarter},{t + 3 * quarter},\\frz-3)"
                f"\\t({t + 3 * quarter},{t + period},\\frz0)"
            )
    return "".join(out)


EXIT_MS = {"fade": 300, "shrink": 260, "slide_down": 300}


def styled_text(
    style,
    text: str,
    *,
    duration: float,
    size: int,
    point: tuple[int, int] | None = None,
    fade_out: int = 0,
    allow_karaoke: bool = True,
    pin: int | None = None,
    line: int | None = None,
    keywords: Keywords | None = None,
    finite: bool = True,
) -> str:
    """一条字幕 / 标题事件的正文：发光、入场 / 出场 / 循环效果、逐字标签、关键词高亮。

    ``point`` 是对齐点坐标，上滑 / 下滑效果需要它；``fade_out`` 是统一加上的淡出毫秒数。
    ``pin`` 给出时把文字固定在 ``point``（用这个 \\an 对齐编号）。
    ``line`` 给出时只输出 ``text`` 的这一行（逐字效果的时间仍按全文分配）。
    ``finite`` 为假表示事件一直显示到片尾（常驻标题），不加出场效果。
    """
    animation = style.animation
    if animation == "karaoke" and not allow_karaoke:
        animation = "typewriter"
    total_ms = max(int(duration * 1000), 1)
    exit_kind = getattr(style, "exit", "none") if finite else "none"
    exit_ms = min(EXIT_MS.get(exit_kind, 0), total_ms // 3)
    tags: list[str] = []
    if style.background_opacity > 0:
        # 底框上下只用字体自带的行高，两行字幕的框正好相接、不重叠。
        tags.append(f"\\ybord{box_padding_y(size)}")
    moving = animation == "slide_up" and point is not None
    slide_out = exit_kind == "slide_down" and point is not None and not moving and exit_ms > 0
    if pin is not None:
        tags.append(f"\\an{pin}")
        if point is not None and not (moving or slide_out):
            tags.append(f"\\pos({point[0]},{point[1]})")
    if style.glow > 0:
        tags.append(f"\\blur{style.glow:g}")
    if style.shadow > 0 and getattr(style, "shadow_angle", 45.0) != 45.0:
        tags.append(shadow_offset_tags(style))
    fade_in = 0
    if animation == "fade":
        fade_in = min(250, int(duration * 300))
    elif animation == "pop":
        fade_in = 80
        tags.append("\\fscx70\\fscy70\\t(0,180,\\fscx100\\fscy100)")
    elif moving:
        fade_in = 150
        x, y = point
        tags.append(f"\\move({x},{y + max(20, size // 2)},{x},{y},0,240)")
    loop = getattr(style, "loop", "none")
    if loop != "none":
        tags.append(_loop_tags(loop, LOOP_START, total_ms - exit_ms))
    if exit_kind == "fade" or (exit_kind == "slide_down" and not slide_out):
        fade_out = max(fade_out, exit_ms)
    elif exit_kind == "shrink" and exit_ms:
        tags.append(f"\\t({total_ms - exit_ms},{total_ms},\\fscx0\\fscy0)")
        fade_out = max(fade_out, exit_ms // 2)
    elif slide_out:
        x, y = point
        tags.append(f"\\move({x},{y},{x},{y + max(20, size // 2)},{total_ms - exit_ms},{total_ms})")
        fade_out = max(fade_out, exit_ms)
    if fade_in or fade_out:
        tags.append(f"\\fad({fade_in},{fade_out})")
    prefix = f"{{{''.join(tags)}}}" if tags else ""
    marker = _Marker(text, style, size, keywords)
    if animation == "typewriter":
        reveal = min(max(duration, 0.2) * 0.7, max(0.3, len(text.strip()) * 0.07))
        return prefix + _reveal_tags(text, "ko", reveal, line=line, marker=marker)
    if animation == "karaoke":
        return prefix + _reveal_tags(text, "kf", max(duration, 0.2), line=line, marker=marker)
    if line is not None:
        start, end = _line_ranges(text)[line]
        return prefix + marker.plain(text[start:end], start)
    return prefix + marker.plain(text)


def shadow_offset_tags(style) -> str:
    """按阴影方向把阴影距离拆成横向、纵向偏移。"""
    angle = math.radians(getattr(style, "shadow_angle", 45.0))
    distance = style.shadow * math.sqrt(2)  # 45° 时和 ASS 默认的 (d, d) 一样
    return f"\\xshad{distance * math.cos(angle):.1f}\\yshad{distance * math.sin(angle):.1f}"


def line_points(code: int, point: tuple[int, int], count: int, step: float) -> list[tuple[int, int]]:
    """多行文字逐行定位时每行的对齐点：底部对齐从下往上排，顶部从上往下，中间以中心对称。"""
    x, y = point
    row = (code - 1) // 3
    if row == 0:
        return [(x, round(y - (count - 1 - i) * step)) for i in range(count)]
    if row == 2:
        return [(x, round(y + i * step)) for i in range(count)]
    return [(x, round(y + (i - (count - 1) / 2) * step)) for i in range(count)]


LINE_HEIGHT = 1.0
"""ASS 里一行字的高度约等于字号（字体的上下伸部按字号缩放）。"""


def dialogues(
    layer: int,
    start: float | str,
    end: float | str,
    name: str,
    style,
    text: str,
    *,
    size: int,
    point: tuple[int, int] | None = None,
    fade_out: int = 0,
    allow_karaoke: bool = True,
    pin: int | None = None,
    code: int | None = None,
    keywords: Keywords | None = None,
) -> list[str]:
    """一条文字对应的 Dialogue 行。层号从 ``layer`` 起占用 4 层：阴影、第二层描边、底框、文字。

    - 阴影要模糊、或者有第二层描边时，阴影单独画在最下面一层（只画阴影）。
    - 第二层描边单独一层：描边加粗、换颜色，叠在文字下面。
    - 逐字效果（打字、卡拉 OK）开了底框时每段各画一个框，叠在一起会出现条纹，
      这时底框单独画一层（只画框、文字透明），上面再叠一层不带框的逐字文字。
    - 设了行间距的多行文字逐行定位，每行一条（需要 ``point`` 和对齐编号 ``code`` / ``pin``）。
    """
    begin = start if isinstance(start, str) else _ass_time(start)
    finish = end if isinstance(end, str) else _ass_time(end)
    finite = not isinstance(start, str) and not isinstance(end, str)
    duration = (end - start) if finite else 3.0
    animation = style.animation
    if animation == "karaoke" and not allow_karaoke:
        animation = "typewriter"
    align_code = pin or code
    line_count = text.count("\n") + 1
    split = getattr(style, "line_spacing", 0) > 0 and line_count > 1 and point is not None and align_code
    rows: list[tuple[int | None, tuple[int, int] | None, int | None]]
    if split:
        step = size * LINE_HEIGHT + style.line_spacing
        rows = list(enumerate(line_points(align_code, point, line_count, step)))  # type: ignore[arg-type]
        rows = [(i, p, align_code) for i, p in rows]
    else:
        rows = [(None, point, pin)]

    boxed = style.background_opacity > 0
    outline2 = getattr(style, "outline2", 0.0) if not boxed else 0.0
    blur = getattr(style, "shadow_blur", 0.0)
    shadow_layer = not boxed and style.shadow > 0 and (blur > 0 or outline2 > 0)
    box_layer = boxed and animation in ("typewriter", "karaoke")
    alpha = _alpha(style.opacity)

    def body(st, row, at, pinned) -> str:
        return styled_text(
            st, text, duration=duration, size=size, point=at, fade_out=fade_out, allow_karaoke=allow_karaoke,
            pin=pinned, line=row, keywords=keywords, finite=finite,
        )

    def event(level: int, extra: str, content: str) -> str:
        return f"Dialogue: {layer + level},{begin},{finish},{name},,0,0,0,,{extra}{content}"

    out: list[str] = []
    for row, at, pinned in rows:
        main = body(style, row, at, pinned)
        if shadow_layer:
            width = style.outline + outline2
            # 阴影层：只画阴影（文字和描边透明），模糊只作用在这一层。
            shadow_style = style.model_copy(update={"glow": 0.0})
            tags = f"{{\\1a&HFF&\\2a&HFF&\\3a&HFF&\\bord{width:g}}}"
            text_body = body(shadow_style, row, at, pinned)
            if blur > 0:
                text_body = _insert_tags(text_body, f"\\blur{blur:g}")
            out.append(event(0, tags, text_body))
        if outline2 > 0:
            tags = f"\\bord{style.outline + outline2:g}\\3c{_bgr(style.outline2_color)}\\3a&H{alpha}&\\shad0"
            out.append(event(1, "", _insert_tags(main, tags)))
        if box_layer:
            plain = style.model_copy(update={"animation": "fade" if animation == "typewriter" else "none", "glow": 0})
            box = body(plain, row, at, pinned)
            out.append(event(2, "{\\1a&HFF&\\2a&HFF&\\4a&HFF&}", box))
            out.append(event(3, "{\\3a&HFF&\\4a&HFF&}", main))
            continue
        out.append(event(3, "", _insert_tags(main, "\\shad0") if shadow_layer else main))
    return out


def _insert_tags(content: str, tags: str) -> str:
    """把标签放进正文开头的覆盖块（没有的话新建一个），放在已有标签后面以覆盖它们。"""
    if content.startswith("{"):
        close = content.index("}")
        return content[:close] + tags + content[close:]
    return "{" + tags + "}" + content


_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""


def to_ass(
    cues: list[Cue],
    style: SubtitleStyle,
    *,
    width: int,
    height: int,
    family: str,
    overlays: list | None = None,
    overlay_family: str | None = None,
    margin_l: int = 60,
    margin_r: int = 60,
    extra_styles: list[str] | None = None,
    extra_events: list[str] | None = None,
) -> str:
    """生成 ASS。``overlays`` 是画面上方的文字叠加（卖点、价格、购买引导）。

    ``margin_l`` / ``margin_r`` 是左右留白（像素），交付平台的安全区会把它们加大。
    ``style.margin`` 必须已经换算成像素（见 ``render.subtitle_margin``）。
    """
    margin_v = style.margin or 0
    code = alignment(style.position, style.align)
    styles = [ass_style("Default", family, style.size, style, code, margin_l, margin_r, margin_v)]
    events: list[str] = []
    for index, item in enumerate(overlays or []):
        if not item.enabled or not item.text.strip():
            continue
        name = f"Overlay{index}"
        position = alignment(item.position)
        styles.append(
            f"Style: {name},{overlay_family or family},{item.size},{_ass_color(item.color)},&H000000FF,"
            f"{_ass_color(item.outline_color)},&H64000000,-1,0,0,0,100,100,0,0,1,3,0,"
            f"{position},{margin_l},{margin_r},{item.margin},1"
        )
        end = "" if item.duration is None else _ass_time(item.start + item.duration)
        events.append(
            f"Dialogue: 10,{_ass_time(item.start)},{end or _ass_time(1e6)},{name},,0,0,0,,"
            f"{_ass_escape(item.text.strip())}"
        )
    styles += extra_styles or []
    events += extra_events or []
    point = anchor(code, width, height, margin_l, margin_r, margin_v)
    keywords = (
        Keywords(tuple(style.keywords), style.keyword_color, style.keyword_scale) if style.keywords else None
    )
    lines = [
        line
        for c in cues
        for line in dialogues(
            0, c.start, c.end, "Default", style, c.text, size=style.size, point=point, code=code, keywords=keywords
        )
    ]
    return (
        _HEADER.format(width=width, height=height)
        + "\n".join(styles)
        + "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(events + lines)
        + "\n"
    )


def title_layers(
    titles,
    *,
    width: int,
    height: int,
    families: dict[str, str],
    headline_margin: int,
    shot_margin: int,
    margin_l: int,
    margin_r: int,
    fallback_title: str = "",
    shot_titles: list[tuple[str, float, float]] | None = None,
    total: float | None = None,
) -> tuple[list[str], list[str]]:
    """标题的 ASS 样式和事件：常驻大标题、片头标题卡、镜头小标题。

    ``families`` 是三种标题各自用的字体族名（键 headline / intro / shot）。
    ``shot_titles`` 是每个镜头的（小标题, 开始秒, 结束秒）；边距已按安全区换算好。
    ``total`` 是成片时长：常驻大标题显示到这里（循环效果持续到这里、结尾做出场效果）；不知道时一直显示。
    片头标题卡开着时，常驻大标题在片头结束后才出现，避免重叠。
    """
    styles: list[str] = []
    events: list[str] = []
    forever = _ass_time(1e6)
    headline = titles.headline
    intro = titles.intro
    intro_text = (intro.text or headline.text or fallback_title).strip() if intro.enabled else ""
    intro_end = intro.duration if intro_text else 0.0

    if headline.enabled and headline.text.strip():
        code = alignment("top", headline.align)
        styles.append(
            ass_style("Headline", families["headline"], headline.size, headline, code, margin_l, margin_r, headline_margin)
        )
        events += dialogues(
            30, intro_end if total else _ass_time(intro_end), total or forever, "Headline", headline, headline.text.strip(),
            size=headline.size, point=anchor(code, width, height, margin_l, margin_r, headline_margin),
            allow_karaoke=False, code=code,
        )

    shot = titles.shot
    shot_lines = [(text.strip(), start, end) for text, start, end in shot_titles or [] if text.strip()]
    if shot_lines:
        code = alignment(shot.position, shot.align)
        styles.append(ass_style("ShotTitle", families["shot"], shot.size, shot, code, margin_l, margin_r, shot_margin))
        point = anchor(code, width, height, margin_l, margin_r, shot_margin)
        for text, start, end in shot_lines:
            begin = max(start, intro_end)
            if end - begin < 0.2:
                continue
            events += dialogues(
                20, begin, end, "ShotTitle", shot, text, size=shot.size, point=point, fade_out=150,
                allow_karaoke=False, code=code,
            )

    if intro_text:
        end = _ass_time(intro.duration)
        fade_out = 400
        sub_size = max(24, intro.size * 45 // 100)
        styles.append(ass_style("Intro", families["intro"], intro.size, intro, 5, margin_l, margin_r, 0))
        sub_style = intro.model_copy(
            update={"bold": False, "outline": intro.outline / 2, "shadow": intro.shadow / 2, "underline": False}
        )
        styles.append(ass_style("IntroSub", families["intro"], sub_size, sub_style, 5, margin_l, margin_r, 0))
        if intro.dim > 0:
            # 全屏半透明黑色矩形压暗画面，让标题更醒目。
            events.append(
                f"Dialogue: 40,0:00:00.00,{end},Intro,,0,0,0,,"
                f"{{\\an7\\pos(0,0)\\p1\\bord0\\shad0\\blur0\\1c&H000000&\\1a&H{_alpha(intro.dim)}&"
                f"\\fad(300,{fade_out})}}m 0 0 l {width} 0 {width} {height} 0 {height}{{\\p0}}"
            )
        has_sub = bool(intro.subtitle.strip())
        lift = intro.size // 2 if has_sub else 0
        main_point = (width // 2, height // 2 - lift)
        events += dialogues(
            50, 0.0, intro.duration, "Intro", intro, intro_text, size=intro.size, point=main_point,
            fade_out=fade_out, allow_karaoke=False, pin=5,
        )
        if has_sub:
            sub_point = (width // 2, height // 2 + intro.size // 2 + intro.size * 45 // 200)
            events += dialogues(
                50, 0.0, intro.duration, "IntroSub", sub_style, intro.subtitle.strip(), size=sub_size,
                point=sub_point, fade_out=fade_out, allow_karaoke=False, pin=5,
            )
    return styles, events
