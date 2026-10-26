"""字幕：切分、按像素宽度换行、生成 ASS / SRT。

每个镜头的文案按标点切成若干条字幕，在该镜头的配音时间段内按字数比例分配时长。
换行按真实字体的像素宽度计算：中日韩文字可以在任意字之间断开，拉丁文字按词断开。
"""

from __future__ import annotations

import re
import unicodedata
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
    for candidate in sorted(fonts_dir.glob("*.[ot]tf")):
        if candidate == preferred:
            continue
        left = missing_glyphs(candidate, text)
        if len(left) < len(best_missing):
            best, best_missing = candidate, left
            if not left:
                break
    return best, best_missing


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
        if current and len(wrap(candidate, font_path, size, max_width)) > max_lines:
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
        lines = wrap(group, font_path, size, max_width)
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


def to_ass(
    cues: list[Cue],
    style: SubtitleStyle,
    *,
    width: int,
    height: int,
    family: str,
    overlays: list | None = None,
    margin_l: int = 60,
    margin_r: int = 60,
    extra_styles: list[str] | None = None,
    extra_events: list[str] | None = None,
) -> str:
    """生成 ASS。``overlays`` 是画面上方的文字叠加（卖点、价格、购买引导）。

    ``margin_l`` / ``margin_r`` 是左右留白（像素），交付平台的安全区会把它们加大。
    """
    alignment = {"bottom": 2, "middle": 5, "top": 8}[style.position]
    overlay_styles: list[str] = []
    overlay_events: list[str] = []
    for index, item in enumerate(overlays or []):
        if not item.enabled or not item.text.strip():
            continue
        name = f"Overlay{index}"
        position = {"bottom": 2, "middle": 5, "top": 8}[item.position]
        overlay_styles.append(
            f"Style: {name},{family},{item.size},{_ass_color(item.color)},&H000000FF,"
            f"{_ass_color(item.outline_color)},&H64000000,1,0,0,0,100,100,0,0,1,3,0,"
            f"{position},{margin_l},{margin_r},{item.margin},1"
        )
        end = "" if item.duration is None else _ass_time(item.start + item.duration)
        overlay_events.append(
            f"Dialogue: 1,{_ass_time(item.start)},{end or _ass_time(1e6)},{name},,0,0,0,,"
            f"{_ass_escape(item.text.strip())}"
        )
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{family},{style.size},{_ass_color(style.color)},&H000000FF,{_ass_color(style.outline_color)},&H64000000,0,0,0,0,100,100,0,0,1,{style.outline:g},0,{alignment},{margin_l},{margin_r},{style.margin},1
"""
    overlay_styles += extra_styles or []
    overlay_events += extra_events or []
    if overlay_styles:
        header += "\n".join(overlay_styles) + "\n"
    header += """
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [
        f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,{_ass_escape(c.text)}"
        for c in cues
    ]
    return header + "\n".join(overlay_events + lines) + "\n"


def to_srt(cues: list[Cue]) -> str:
    blocks = [
        f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n" for i, c in enumerate(cues, start=1)
    ]
    return "\n".join(blocks)


def _alpha(opacity: float) -> str:
    """ASS 的透明度：00 不透明，FF 全透明。"""
    return f"{round((1 - max(0.0, min(1.0, opacity))) * 255):02X}"


def title_layers(
    titles,
    *,
    width: int,
    height: int,
    family: str,
    headline_margin: int,
    shot_margin: int,
    margin_l: int,
    margin_r: int,
    fallback_title: str = "",
    shot_titles: list[tuple[str, float, float]] | None = None,
) -> tuple[list[str], list[str]]:
    """标题的 ASS 样式和事件：常驻大标题、片头标题卡、镜头小标题。

    ``shot_titles`` 是每个镜头的（小标题, 开始秒, 结束秒）；边距已按安全区换算好。
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
        boxed = headline.background_opacity > 0
        border_style = 3 if boxed else 1
        box_colour = (
            f"&H{_alpha(headline.background_opacity)}{_ass_color(headline.background)[4:]}"
            if boxed
            else _ass_color(headline.outline_color)
        )
        outline = 14 if boxed else max(2, headline.size // 14)
        styles.append(
            f"Style: Headline,{family},{headline.size},{_ass_color(headline.color)},&H000000FF,"
            f"{box_colour},&H64000000,1,0,0,0,100,100,0,0,{border_style},{outline},0,8,"
            f"{margin_l},{margin_r},{headline_margin},1"
        )
        events.append(
            f"Dialogue: 3,{_ass_time(intro_end)},{forever},Headline,,0,0,0,,{_ass_escape(headline.text.strip())}"
        )

    shot = titles.shot
    shot_lines = [(text.strip(), start, end) for text, start, end in shot_titles or [] if text.strip()]
    if shot_lines:
        alignment = {"bottom": 2, "middle": 5, "top": 8}[shot.position]
        styles.append(
            f"Style: ShotTitle,{family},{shot.size},{_ass_color(shot.color)},&H000000FF,"
            f"{_ass_color(shot.outline_color)},&H64000000,1,0,0,0,100,100,0,0,1,{max(2, shot.size // 14)},0,"
            f"{alignment},{margin_l},{margin_r},{shot_margin},1"
        )
        for text, start, end in shot_lines:
            begin = max(start, intro_end)
            if end - begin < 0.2:
                continue
            events.append(
                f"Dialogue: 2,{_ass_time(begin)},{_ass_time(end)},ShotTitle,,0,0,0,,{{\\fad(150,150)}}{_ass_escape(text)}"
            )

    if intro_text:
        fade_in, fade_out = 300, 400
        end = _ass_time(intro.duration)
        styles.append(
            f"Style: Intro,{family},{intro.size},{_ass_color(intro.color)},&H000000FF,&H00000000,&H64000000,"
            f"1,0,0,0,100,100,0,0,1,{max(2, intro.size // 18)},0,5,{margin_l},{margin_r},0,1"
        )
        styles.append(
            f"Style: IntroSub,{family},{max(24, intro.size * 45 // 100)},{_ass_color(intro.color)},&H000000FF,"
            f"&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,0,5,{margin_l},{margin_r},0,1"
        )
        if intro.dim > 0:
            # 全屏半透明黑色矩形压暗画面，让标题更醒目。
            events.append(
                f"Dialogue: 4,0:00:00.00,{end},Intro,,0,0,0,,"
                f"{{\\an7\\pos(0,0)\\p1\\bord0\\shad0\\1c&H000000&\\1a&H{_alpha(intro.dim)}&"
                f"\\fad({fade_in},{fade_out})}}m 0 0 l {width} 0 {width} {height} 0 {height}{{\\p0}}"
            )
        lift = intro.size // 2 if intro.subtitle.strip() else 0
        events.append(
            f"Dialogue: 5,0:00:00.00,{end},Intro,,0,0,0,,"
            f"{{\\an5\\pos({width // 2},{height // 2 - lift})\\fad({fade_in},{fade_out})}}{_ass_escape(intro_text)}"
        )
        if intro.subtitle.strip():
            events.append(
                f"Dialogue: 5,0:00:00.00,{end},IntroSub,,0,0,0,,"
                f"{{\\an5\\pos({width // 2},{height // 2 + intro.size // 2 + intro.size * 45 // 200})"
                f"\\fad({fade_in},{fade_out})}}{_ass_escape(intro.subtitle.strip())}"
            )
    return styles, events
