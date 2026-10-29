"""读音纠正：配音前把容易读错的词换成同音字，字幕仍显示原文。

配音服务（尤其是 Edge 等免费服务）不支持指定多音字读音，例如把「打着飞的」的「的」读成 de。
这里在合成前做文字替换：用户的读音纠正表（:attr:`Project.pronunciations`）优先，长词优先、
互不重叠；中文项目再套用几条内置的安全规则。替换后的文字只交给配音服务，字幕、时长估算
仍用原文；配音服务返回的逐词时间戳按 :func:`respell` 给出的位置映射对回原文。
"""

from __future__ import annotations

import re

from shotwright.core.models import Project, Pronunciation
from shotwright.providers.base import LLMProvider, ProviderError

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


PROMPT = """你是中文配音校对。下面的文案会交给语音合成朗读，合成引擎常把多音字、网络用语、专有名词读错
（例如「打着飞的」的「的」应读 dī，却被读成 de）。找出在这段文案的语境里容易被读错的词，给出替换写法：
- word：文案里原样出现的连续文字，尽量短（2–4 个字，包含读错的字和能确定语境的前后字）；
- say：读音相同、引擎不会读错的写法，优先用常见的同音字替换读错的那个字，字数和 word 相同；
- note：正确读音的拼音（带声调）和一句说明。
只挑真正可能读错的，没有就返回空列表；不要挑读音没有歧义的常用词。
只输出 JSON：{"items": [{"word": "飞的", "say": "飞滴", "note": "fēi dī，坐飞机"}]}"""

MAX_SUGGESTIONS = 20


def suggest(llm: LLMProvider, project: Project) -> list[Pronunciation]:
    """让大模型找出文案里容易读错的词，返回新的读音纠正（已有的词不重复）。"""
    from shotwright.services import speech
    from shotwright.services.storyboard import _extract_json

    script = "\n".join(speech.plain(shot.text) for shot in project.shots if shot.text.strip())
    if not script.strip():
        raise ProviderError("项目里还没有文案")
    data = _extract_json(
        llm.complete(
            [{"role": "system", "content": PROMPT}, {"role": "user", "content": script[:6000]}],
            json_mode=True,
        )
    )
    known = {item.word for item in project.pronunciations}
    found: list[Pronunciation] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or "").strip()[:20]
        say = str(item.get("say") or "").strip()[:40]
        if not word or not say or word == say or word not in script or word in known:
            continue
        known.add(word)
        found.append(Pronunciation(word=word, say=say, note=str(item.get("note") or "").strip()[:60]))
    return found[:MAX_SUGGESTIONS]


def builtin_notes() -> list[str]:
    """内置规则的说明（界面上提示用）。"""
    return [note for _, _, note in BUILTIN]
