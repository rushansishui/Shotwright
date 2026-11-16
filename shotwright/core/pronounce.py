"""读音纠正：配音前把容易读错的词换成同音字，字幕仍显示原文。

配音服务（尤其是 Edge 等免费服务）不支持指定多音字读音，例如把「打着飞的」的「的」读成 de。
这里在合成前做文字替换：用户的读音纠正表（:attr:`Project.pronunciations`）优先，长词优先、
互不重叠；中文项目再套用几条内置的安全规则。替换后的文字只交给配音服务，字幕、时长估算
仍用原文；配音服务返回的逐词时间戳按 :func:`respell` 给出的位置映射对回原文。
"""

from __future__ import annotations

import re

from shotwright.core.models import Project

BUILTIN: list[tuple[re.Pattern[str], str, str]] = [
    # 打飞的、打着飞的、坐个飞的：「的」读 dī（坐飞机）。「会飞的鸟」这类不受影响。
    (re.compile(r"(?:[打坐搭乘][着了个过]{0,2})飞(?P<x>的)"), "滴", "飞的 fēidī（坐飞机）"),
    # 打的去、打的回来：「的」读 dī（坐出租车）。
    (re.compile(r"打(?P<x>的)(?=[去回到来走过上])"), "滴", "打的 dădī（坐出租车）"),
]
"""内置规则：（正则, 替换 x 组的文字, 说明）。只收误判几乎不可能的搭配。"""


def _builtin_enabled(project: Project) -> bool:
    return project.language.lower().startswith("zh")


def respell(project: Project, text: str) -> tuple[str, list[int]]:
    """返回（交给配音服务的文字, 每个字对应原文的位置）。没有需要替换的词时原样返回。"""
    taken = [False] * len(text)
    spans: list[tuple[int, int, str]] = []

    def claim(a: int, b: int, say: str) -> None:
        if a < b and not any(taken[a:b]):
            spans.append((a, b, say))
            for k in range(a, b):
                taken[k] = True

    entries = sorted(project.pronunciations, key=lambda item: len(item.word), reverse=True)
    for entry in entries:
        start = 0
        while (index := text.find(entry.word, start)) >= 0:
            claim(index, index + len(entry.word), entry.say)
            start = index + len(entry.word)
    if _builtin_enabled(project):
        for pattern, say, _ in BUILTIN:
            for match in pattern.finditer(text):
                claim(match.start("x"), match.end("x"), say)
    if not spans:
        return text, list(range(len(text)))
    spans.sort()
    out: list[str] = []
    mapping: list[int] = []
    position = 0
    for a, b, say in spans:
        out.append(text[position:a])
        mapping += range(position, a)
        out.append(say)
        mapping += [a + (k * (b - a)) // len(say) for k in range(len(say))]
        position = b
    out.append(text[position:])
    mapping += range(position, len(text))
    return "".join(out), mapping


def spoken(project: Project, text: str) -> str:
    """交给配音服务的文字。"""
    return respell(project, text)[0]


def builtin_notes() -> list[str]:
    """内置规则的说明（界面上提示用）。"""
    return [note for _, _, note in BUILTIN]
