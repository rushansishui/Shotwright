"""分镜生成：主题 → 文案 → 镜头列表。

两种方式：

* :func:`split_script` 不联网，把现成文案按句子切成镜头；
* :func:`write_storyboard` 调用大模型，先定全片设定（主体代号与外观、场景、
  风格、禁止项），再产出每个镜头的文案、英文检索词、画面描述、运镜和衔接说明，
  出稿后自检一次，不合格时重写一次。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from shotwright.core.models import VIDEO_TYPE_NAMES, Look, LookSubject, Shot, ShotVisual
from shotwright.providers.base import LLMProvider, ProviderError
from shotwright.providers.tts.silent import estimate_seconds

log = logging.getLogger("shotwright.storyboard")

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


SYSTEM_PROMPT = """你是短视频编导兼分镜师。根据视频类型和诉求，先定下全片设定，再写口播文案并拆成分镜。

一、全片设定（look）
- subjects：视频里反复出现的产品、人物、物品、场所，每个给一个代号（产品用 P1、P2，人物用 A、B，物品用 O1、O2，场所用 S1），name 写简短名称，kind 取 product / person / object / place 之一，appearance 用{plang}写清外观：颜色、材质、形状、尺寸、服装、发型、年龄感等，具体到不同镜头生成出来是同一个东西、同一个人。诉求或主体库里已经写了外观的，以它为准，不要改动。
- scene：主要场景、时间和环境特征；style：画面质感、色调、光线和整体镜头语言；avoid：不要出现的内容。都用{plang}写。
- 品牌名不写进外观和画面描述，改写成外观特征。

二、文案
- 用 {language} 写，口语化，开头 1–2 句要抓人。
- {length_rule}
- 每个镜头 1–2 句话，朗读 3–8 秒。

三、每个镜头
- role：镜头类型，取 hook（开场钩子）/ product（产品亮相）/ demo（使用演示）/ scene（场景氛围）/ people（人物）/ explain（原理示意）/ talking（口播）/ cta（结尾引导）之一。{structure}
- text：这个镜头的文案。
- terms：2–3 个英文素材检索词（用于图库，具体、可拍到的画面，例如 "city night traffic"）。
- prompt：用{plang}写这个镜头的画面描述，{prompt_len}。依次写清：主体（用代号指代，例如「P1 放在浅色木桌中央」）、动作（按镜头时长拆成 2–3 个先后发生的动作）、场景细节、构图与景别（特写 / 近景 / 中景 / 全景）、光线。不要写运镜和全片风格（另有字段），不要出现品牌名和画面文字。
- still：用{plang}写这个镜头最有代表性的一个瞬间，用于 AI 图片和关键帧：静态画面，不写动作过程，{still_len}，同样用代号指代主体。
- camera：运镜，取 static / push_in / pull_out / pan / truck / orbit / crane / handheld 之一。
- link：用{plang}一句话写衔接：开头怎样承接上一个镜头（动作延续、视线延续、景别递进、相同元素或明确转场），结尾留给下一个镜头什么；第一个镜头写开场方式。
- 相邻镜头要接得上：同一主体始终用同一个代号，景别有变化，动作有延续。

只输出 JSON，不要任何解释，格式：
{{"title": "标题", "look": {{"subjects": [{{"code": "P1", "name": "名称", "kind": "product", "appearance": "外观"}}], "scene": "场景", "style": "风格", "avoid": "禁止项"}}, "shots": [{{"role": "hook", "text": "文案", "terms": ["..."], "prompt": "画面描述", "still": "静态画面", "camera": "push_in", "link": "衔接"}}]}}"""

_PROMPT_LANG = {"zh": "中文", "en": "英文"}
_PROMPT_LEN = {"zh": "约 120–200 字", "en": "60–120 个英文单词"}
_STILL_LEN = {"zh": "约 60–100 字", "en": "40–70 个英文单词"}
_MIN_PROMPT = {"zh": 90, "en": 45}
"""自检时画面描述的最低长度（中文按字，英文按词），略低于要求，给模型留余地。"""
_ASPECT_NAMES = {"9:16": "竖屏 9:16", "16:9": "横屏 16:9", "1:1": "方形 1:1"}
_ROLE_NAMES = {"product": "产品", "person": "人物", "logo": "Logo", "style": "风格"}
STRUCTURES: dict[str, str] = {
    "product_ad": "按这个结构安排镜头：hook（痛点或效果对比，前 3 秒）→ product（产品亮相）→ demo（逐个演示卖点，每个卖点 1–2 个镜头）→ people（真实使用场景或用户反馈）→ cta（价格与行动引导）。",
    "brand": "按这个结构安排镜头：hook（引人入胜的画面或一句理念）→ scene / people（品牌故事，按时间或人物展开）→ product（作品或产品特写）→ cta（理念落点与 Logo）。",
    "explainer": "按这个结构安排镜头：hook（抛出问题或反常识现象）→ explain（逐条讲原理，每条配示意画面）→ demo（实际例子或对比）→ cta（结论与实用建议）。",
    "talking": "按这个结构安排镜头：hook（主讲人一句话开场）→ talking（观点或步骤，主讲人出镜为主）→ 讲到具体事物时插入 demo / scene 画面 → cta（总结与互动提问）。",
    "footage": "按原片内容安排镜头：hook（原片里最吸引人的一刻）→ product / demo（原片主体和动作）→ 原片不够时补 scene 镜头 → cta（结尾引导）。",
}
"""各视频类型的叙事结构，写进分镜提示词。"""

ROLE_SOURCES: dict[str, str] = {
    "hook": "ai",
    "product": "ai",
    "demo": "ai",
    "cta": "ai",
    "explain": "image",
    "scene": "stock",
    "people": "stock",
    "talking": "stock",
}
"""镜头类型对应的默认画面来源：产品相关的镜头用 AI 生成（能参照产品图），氛围和人物用素材库，
原理示意用 AI 图片。对应的生成服务没配置时一律用素材检索。"""

_ROLES = set(ROLE_SOURCES)
_CAMERAS = {"static", "push_in", "pull_out", "pan", "truck", "orbit", "crane", "handheld"}
_NEEDS_SUBJECTS = {"product_ad", "brand", "talking", "footage"}


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


@dataclass
class Storyboard:
    title: str
    shots: list[Shot]
    look: Look = field(default_factory=Look)
    issues: list[str] = field(default_factory=list)
    """自检后仍然存在的问题（已经让大模型重写过一次）。"""


def _prompt_size(text: str, lang: str) -> int:
    if lang == "zh":
        return len(_CJK.findall(text))
    return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text))


def _user_message(
    subject: str,
    *,
    video_type: str | None,
    aspect: str | None,
    duration: float | None,
    references: list[tuple[str, str]] | None,
) -> str:
    lines = []
    kind = VIDEO_TYPE_NAMES.get(video_type or "")
    if kind:
        lines.append(f"视频类型：{kind}")
    if aspect:
        lines.append(f"画面比例：{_ASPECT_NAMES.get(aspect, aspect)}")
    if duration:
        lines.append(f"目标时长：约 {duration:g} 秒")
    if references:
        lines.append("主体库（已上传参考图，外观以参考图为准）：")
        lines += [f"- {_ROLE_NAMES.get(role, role)}：{label or '未写说明'}" for role, label in references]
    lines.append(f"诉求原文：\n{subject}")
    return "\n".join(lines)


def _parse(data: dict, subject: str) -> Storyboard:
    title = str(data.get("title") or subject)[:120]
    look = _parse_look(data.get("look"))
    result: list[Shot] = []
    for item in data.get("shots") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        terms = [str(t).strip() for t in item.get("terms") or [] if str(t).strip()][:5]
        camera = str(item.get("camera") or "auto").strip().lower()
        role = str(item.get("role") or "").strip().lower()
        result.append(
            Shot(
                text=text[:500],
                role=role if role in _ROLES else None,  # type: ignore[arg-type]
                visual=ShotVisual(
                    mode="stock",
                    terms=terms,
                    prompt=str(item.get("prompt") or "").strip()[:1500],
                    image_prompt=str(item.get("still") or "").strip()[:1000],
                    camera=camera if camera in _CAMERAS else "auto",  # type: ignore[arg-type]
                    link=str(item.get("link") or "").strip()[:400],
                ),
            )
        )
    if not result:
        raise ProviderError("大模型没有返回任何镜头")
    return Storyboard(title=title, shots=result, look=look)


def _parse_look(raw) -> Look:
    if not isinstance(raw, dict):
        return Look()
    subjects: list[LookSubject] = []
    seen: set[str] = set()
    for item in raw.get("subjects") or []:
        if not isinstance(item, dict):
            continue
        code = re.sub(r"[^A-Za-z0-9_-]", "", str(item.get("code") or ""))[:12]
        if not code or not code[0].isalpha() or code in seen:
            continue
        kind = str(item.get("kind") or "object")
        try:
            subjects.append(
                LookSubject(
                    code=code,
                    name=str(item.get("name") or ""),
                    kind=kind if kind in ("product", "person", "object", "place") else "object",  # type: ignore[arg-type]
                    appearance=str(item.get("appearance") or ""),
                )
            )
        except ValueError:
            continue
        seen.add(code)
        if len(subjects) >= 12:
            break
    return Look(
        subjects=subjects,
        scene=str(raw.get("scene") or ""),
        style=str(raw.get("style") or ""),
        avoid=str(raw.get("avoid") or ""),
    )


def suggest_sources(shots: list[Shot], available: set[str]) -> None:
    """按镜头类型给每个镜头选默认画面来源。``available`` 是已配置的来源（ai、image）。

    只是默认值：AI 来源仍然要用户确认收费才会生成，没确认时渲染会改用素材检索。
    """
    for shot in shots:
        wanted = ROLE_SOURCES.get(shot.role or "", "stock")
        if wanted in available:
            shot.visual.mode = wanted  # type: ignore[assignment]


def check_storyboard(
    board: Storyboard, *, lang: str = "zh", duration: float | None = None, video_type: str | None = None
) -> list[str]:
    """分镜出稿自检：总时长、画面描述长度、主体代号、衔接说明。返回问题列表（空表示合格）。"""
    issues: list[str] = []
    if duration:
        spoken = sum(estimate_seconds(shot.text) + 0.3 for shot in board.shots)
        if abs(spoken - duration) > max(5.0, duration * 0.3):
            issues.append(f"文案朗读约 {spoken:.0f} 秒，目标约 {duration:g} 秒，请调整文案长度或镜头数")
    if video_type in _NEEDS_SUBJECTS and not board.look.subjects:
        issues.append("全片设定缺少主体（产品或人物）及其外观描述")
    codes = {subject.code for subject in board.look.subjects}
    minimum = _MIN_PROMPT.get(lang, 45)
    unit = "字" if lang == "zh" else "个词"
    for index, shot in enumerate(board.shots, start=1):
        size = _prompt_size(shot.visual.prompt, lang)
        if size < minimum:
            issues.append(f"镜头 {index} 的画面描述只有 {size} {unit}，要求{_PROMPT_LEN.get(lang, '')}")
        if not shot.visual.link:
            issues.append(f"镜头 {index} 缺少衔接说明")
        unknown = sorted(set(re.findall(r"(?<![A-Za-z0-9])[A-Z]\d{1,2}(?![A-Za-z0-9])", shot.visual.prompt)) - codes)
        if unknown:
            issues.append(f"镜头 {index} 用了全片设定里没有的代号 {'、'.join(unknown)}")
    return issues


def write_storyboard(
    llm: LLMProvider,
    subject: str,
    *,
    language: str = "简体中文",
    prompt_lang: str = "zh",
    shots: int | None = None,
    duration: float | None = None,
    video_type: str | None = None,
    aspect: str | None = None,
    references: list[tuple[str, str]] | None = None,
) -> Storyboard:
    """根据诉求写分镜：全片设定 + 文案 + 每个镜头的画面描述、运镜和衔接。

    出稿后自检一次，不合格时带着问题让大模型重写一次；重写后仍有问题就保留
    问题列表返回，不再重试（避免无限花钱）。
    """
    lang = prompt_lang if prompt_lang in _PROMPT_LANG else "en"
    system = SYSTEM_PROMPT.format(
        language=language,
        length_rule=_length_rule(shots, duration),
        plang=_PROMPT_LANG[lang],
        prompt_len=_PROMPT_LEN[lang],
        still_len=_STILL_LEN[lang],
        structure=STRUCTURES.get(video_type or "", ""),
    )
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": _user_message(
                subject, video_type=video_type, aspect=aspect, duration=duration, references=references
            ),
        },
    ]
    reply = llm.complete(messages, json_mode=True)
    board = _parse(_extract_json(reply), subject)
    board.issues = check_storyboard(board, lang=lang, duration=duration, video_type=video_type)
    if not board.issues:
        return board
    log.info("分镜自检发现 %d 个问题，让大模型重写一次", len(board.issues))
    retry = [
        *messages,
        {"role": "assistant", "content": reply},
        {
            "role": "user",
            "content": "上一版有这些问题，请修正后重新输出完整的 JSON：\n"
            + "\n".join(f"- {issue}" for issue in board.issues),
        },
    ]
    try:
        second = _parse(_extract_json(llm.complete(retry, json_mode=True)), subject)
    except ProviderError as exc:
        log.warning("分镜重写失败，保留第一版：%s", exc)
        return board
    second.issues = check_storyboard(second, lang=lang, duration=duration, video_type=video_type)
    return second if len(second.issues) <= len(board.issues) else board


def generate_storyboard(
    llm: LLMProvider,
    subject: str,
    *,
    language: str = "简体中文",
    shots: int | None = None,
    duration: float | None = None,
    video_type: str | None = None,
) -> tuple[str, list[Shot]]:
    """兼容旧调用：只返回标题和镜头。新代码请用 :func:`write_storyboard`。"""
    board = write_storyboard(
        llm, subject, language=language, shots=shots, duration=duration, video_type=video_type
    )
    return board.title, board.shots
