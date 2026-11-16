"""读音纠正里需要大模型的部分：找出文案里容易读错的词。

纯文本替换（``respell`` / ``spoken``）在 :mod:`shotwright.core.pronounce`——渲染引擎
要用它，不能反过来依赖服务层。这里只保留会调用大模型的「AI 检查」一件事。

旧代码里的 ``from shotwright.services import pronounce`` 仍然可用：下面把纯函数
重新导出，调用方式不变。
"""

from __future__ import annotations

from shotwright.core.models import Project, Pronunciation
from shotwright.core.pronounce import BUILTIN, builtin_notes, respell, spoken
from shotwright.providers.base import LLMProvider, ProviderError

__all__ = ["BUILTIN", "builtin_notes", "respell", "spoken", "suggest"]

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
    from shotwright.core import speech
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
