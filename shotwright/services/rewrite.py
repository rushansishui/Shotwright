"""改写和补全画面描述：单镜头 AI 改写、一键扩写短提示词和老项目。

两者都只改画面相关的字段（画面描述、静态画面描述、运镜、衔接），不改文案；
都会把全片设定和相邻镜头交给大模型，改完的镜头和前后镜头仍然接得上。
锁定的镜头不改。
"""

from __future__ import annotations

import json

from shotwright.core.models import Project, Shot
from shotwright.providers.base import LLMProvider, ProviderError
from shotwright.services import prompting
from shotwright.services.storyboard import (
    _CAMERAS,
    _MIN_PROMPT,
    _PROMPT_LANG,
    _PROMPT_LEN,
    _STILL_LEN,
    _extract_json,
    _parse_look,
    _prompt_size,
)

RULES = """画面描述（prompt）用{plang}写，{prompt_len}：依次写清主体（用全片设定里的代号指代）、动作（按镜头时长拆成 2–3 个先后动作）、场景细节、构图与景别、光线；不写运镜和全片风格，不出现品牌名和画面文字。
静态画面描述（still）用{plang}写，{still_len}：这个镜头最有代表性的一个瞬间，不写动作过程。
运镜（camera）取 static / push_in / pull_out / pan / truck / orbit / crane / handheld 之一。
衔接（link）用{plang}一句话写：开头怎样承接上一个镜头，结尾留给下一个镜头什么；第一个镜头写开场方式。"""

REWRITE_PROMPT = """你是短视频分镜师。按用户的要求只改写一个镜头的画面，保持全片设定不变，并和前后镜头接得上。
{rules}
只输出 JSON：{{"prompt": "...", "still": "...", "camera": "...", "link": "..."}}"""

EXPAND_PROMPT = """你是短视频分镜师。下面是一条视频已有的分镜，有些镜头的画面描述太短或缺少衔接。请补全它们。
{rules}
{look_rule}
只输出 JSON：{{"look": {look_shape}, "shots": [{{"index": 1, "prompt": "...", "still": "...", "camera": "...", "link": "..."}}]}}
shots 里只列出需要补全的镜头，index 是镜头序号（从 1 开始）。"""


def _rules(lang: str) -> str:
    return RULES.format(plang=_PROMPT_LANG[lang], prompt_len=_PROMPT_LEN[lang], still_len=_STILL_LEN[lang])


def _look_json(project: Project) -> str:
    return json.dumps(
        project.look.model_dump(include={"subjects", "scene", "style", "avoid"}), ensure_ascii=False
    )


def _shot_brief(index: int, shot: Shot) -> dict:
    visual = shot.visual
    return {
        "index": index + 1,
        "text": shot.text,
        "prompt": visual.prompt,
        "camera": visual.camera,
        "link": visual.link,
    }


def _apply(shot: Shot, item: dict) -> bool:
    """把大模型给的字段写进镜头；字段为空时保留原值。返回是否有改动。"""
    visual = shot.visual
    before = (visual.prompt, visual.image_prompt, visual.camera, visual.link)
    prompt = str(item.get("prompt") or "").strip()
    if prompt:
        visual.prompt = prompt[:1500]
    still = str(item.get("still") or "").strip()
    if still:
        visual.image_prompt = still[:1000]
    camera = str(item.get("camera") or "").strip().lower()
    if camera in _CAMERAS:
        visual.camera = camera  # type: ignore[assignment]
    link = str(item.get("link") or "").strip()
    if link:
        visual.link = link[:400]
    return before != (visual.prompt, visual.image_prompt, visual.camera, visual.link)


def rewrite_shot(llm: LLMProvider, project: Project, shot: Shot, instruction: str) -> bool:
    """按一句要求改写一个镜头的画面描述、静态画面描述、运镜和衔接。"""
    if shot.visual.locked:
        raise ProviderError("镜头已锁定，请先解锁")
    instruction = instruction.strip()
    if not instruction:
        raise ProviderError("请写下要怎么改")
    lang = prompting.prompt_language(project)
    index = next(i for i, item in enumerate(project.shots) if item.id == shot.id)
    neighbours = {
        "上一个镜头": _shot_brief(index - 1, project.shots[index - 1]) if index > 0 else None,
        "下一个镜头": _shot_brief(index + 1, project.shots[index + 1]) if index + 1 < len(project.shots) else None,
    }
    user = "\n".join(
        [
            f"全片设定：{_look_json(project)}",
            *(f"{name}：{json.dumps(value, ensure_ascii=False)}" for name, value in neighbours.items() if value),
            f"要改写的镜头：{json.dumps(_shot_brief(index, shot), ensure_ascii=False)}",
            f"修改要求：{instruction[:500]}",
        ]
    )
    reply = llm.complete(
        [{"role": "system", "content": REWRITE_PROMPT.format(rules=_rules(lang))}, {"role": "user", "content": user}],
        json_mode=True,
    )
    return _apply(shot, _extract_json(reply))


def needs_expansion(project: Project, shot: Shot) -> bool:
    """画面描述太短或缺少衔接说明的镜头需要补全（锁定、纯色、上传的镜头不算）。"""
    visual = shot.visual
    if visual.locked or visual.mode in ("color", "upload"):
        return False
    lang = prompting.prompt_language(project)
    return _prompt_size(visual.prompt, lang) < _MIN_PROMPT[lang] or not visual.link.strip()


def expand_project(llm: LLMProvider, project: Project, *, only_short: bool = True) -> list[str]:
    """一键补全：短提示词扩写、补衔接说明；没有全片设定时一并生成。返回改动过的镜头 id。"""
    targets = [
        index
        for index, shot in enumerate(project.shots)
        if not shot.visual.locked and (needs_expansion(project, shot) if only_short else shot.visual.mode != "color")
    ]
    need_look = project.look.is_empty()
    if not targets and not need_look:
        return []
    lang = prompting.prompt_language(project)
    look_rule = (
        "项目还没有全片设定：请先根据诉求和文案生成 look（主体代号与外观、场景、风格、禁止项），镜头里用这些代号。"
        if need_look
        else "全片设定已经确定，look 填 null，镜头里沿用已有的代号。"
    )
    look_shape = (
        '{"subjects": [{"code": "P1", "name": "...", "kind": "product", "appearance": "..."}], '
        '"scene": "...", "style": "...", "avoid": "..."}'
        if need_look
        else "null"
    )
    system = EXPAND_PROMPT.format(rules=_rules(lang), look_rule=look_rule, look_shape=look_shape)
    user = "\n".join(
        [
            f"诉求：{project.subject[:2000]}" if project.subject else "",
            f"全片设定：{_look_json(project)}" if not need_look else "",
            "全部镜头：",
            *(json.dumps(_shot_brief(i, s), ensure_ascii=False) for i, s in enumerate(project.shots)),
            f"需要补全的镜头序号：{', '.join(str(i + 1) for i in targets) or '无'}",
        ]
    )
    data = _extract_json(
        llm.complete([{"role": "system", "content": system}, {"role": "user", "content": user}], json_mode=True)
    )
    if need_look:
        look = _parse_look(data.get("look"))
        if not look.is_empty():
            project.look = look
    changed: list[str] = []
    allowed = set(targets)
    for item in data.get("shots") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index")) - 1
        except (TypeError, ValueError):
            continue
        if index not in allowed:
            continue
        shot = project.shots[index]
        if _apply(shot, item):
            changed.append(shot.id)
    return changed
