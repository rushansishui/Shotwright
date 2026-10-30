"""生成前检查：花钱生成 AI 画面之前，找出画面描述里容易翻车的内容，并给出改写建议。

AI 视频模型目前普遍做不好的内容：手部特写（手指变形）、画面里要出现的文字（乱码）、
多人互动（肢体粘连、脸互相串）、复杂或快速的动作（变形、穿模）、一个镜头里塞好几个
连续动作（做不完或顺序错乱）、快速运镜（画面撕裂）。另外配音比模型一次能生成的长度
还长时，要分段续生成，费用成倍、衔接处容易不自然。

只看文字、不调用外部服务、不花钱，结果只是提醒，用户可以照样生成。规则按关键词匹配，
前面紧挨着否定词（「不要出现文字」「no text」）的不算。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from shotwright.core.models import Project, Shot

RISK_RULES: dict[str, tuple[str, ...]] = {
    # 手部：特写、手指动作、拿着小东西。只说「手」太宽泛，不算。
    "hands": (
        r"手部特写",
        r"手的特写",
        r"手指",
        r"双手(?:捧|拿|握|拧|拆|撕|折|剥|弹|敲|打字)",
        r"(?:弹钢琴|弹吉他|打字|敲键盘|系鞋带|穿针|剥开|折纸)",
        r"\bclose-?up of (?:a |the |her |his |their )?hands?\b",
        r"\bhand close-?up\b",
        r"\bfingers?\b",
        r"\b(?:playing (?:the )?(?:piano|guitar)|typing|tying (?:shoe)?laces|threading)\b",
    ),
    # 画面里要出现可读的文字。
    "text": (
        r"(?:写着|印着|标语|招牌上|屏幕上(?:显示|写)|文字|字样|标题字|字幕条|价格牌|价签)",
        r"\b(?:text|words?|letters|lettering|caption|slogan|sign that (?:says|reads)|reads?\s*[\"“])",
        r"\b(?:written|printed) on\b",
    ),
    # 多人互动、人群。
    "people": (
        r"(?:两个人|三个人|几个人|多人|一群人|人群|一家[三四五]口|众人|朋友们|同事们)",
        r"(?:握手|拥抱|击掌|碰杯|牵手|递给|交谈|对话|围坐|互动)",
        r"\b(?:two|three|four|several|many|a group of) (?:people|persons|friends|women|men|kids|children)\b",
        r"\b(?:crowd|handshake|shaking hands|hugging|high-?five|toasting|passing (?:it|the \w+) to)\b",
    ),
    # 复杂、快速或高难度动作。
    "motion": (
        r"(?:跳舞|舞蹈|奔跑|狂奔|跳跃|翻转|翻滚|后空翻|打斗|搏击|体操|杂技|旋转跳|踢球|投篮|冲浪|滑板)",
        r"\b(?:dancing|dances|running|sprinting|jumping|flips?|backflip|somersault|fighting|gymnastics|acrobatic|juggling|skateboard\w*|surfing|kicking)\b",
    ),
    # 快速运镜。
    "camera": (
        r"(?:快速(?:推|拉|摇|移|旋转|切换)|急推|急拉|甩镜|快速环绕|360\s*度(?:旋转|环绕))",
        r"\b(?:whip pan|fast (?:zoom|pan|orbit|dolly)|crash zoom|rapid(?:ly)? (?:zoom|pan|spin)|360[- ]degree (?:spin|orbit))\b",
    ),
}
"""每类风险的匹配规则（不区分大小写）。"""

_SEQUENCE = re.compile(
    r"(?:然后|接着|随后|之后|紧接着|最后|\bthen\b|\bafter that\b|\bfinally\b|\bnext\b)", re.I
)
MAX_SEQUENCE = 2
"""「然后、接着」这类连接词达到这么多个，就算一个镜头里塞了好几个连续动作。"""

_NEGATION = re.compile(
    r"(?:不要|不能|不得|不会|不(?![断停时少])|无|没有|避免|禁止|别|\bno\b|\bnot\b|\bwithout\b|\bavoid\b|\bnever\b)"
    r"[^。，,.;；!?！？\n]{0,8}$",
    re.I,
)
"""同一个分句里、在匹配内容前面不远处的否定词（「不要出现任何文字」「without any text」）。"""

_COMPILED = {code: [re.compile(p, re.I) for p in patterns] for code, patterns in RISK_RULES.items()}

EXTEND_MARGIN = 0.5
"""配音比生成上限长出这么多秒以上才分段，和 visuals._generate_or_extend 一致。"""


@dataclass
class Risk:
    code: str
    """hands / text / people / motion / camera / sequence / duplicate / too_long。"""
    params: dict = field(default_factory=dict)
    """界面按 code 和这些参数显示本地化的说明和改写建议。"""
    message: str = ""
    """中文说明（命令行和日志用）。"""
    advice: str = ""
    """中文改写建议。"""


MESSAGES: dict[str, tuple[str, str]] = {
    "hands": (
        "画面里有手部特写或手指动作，AI 视频经常把手指画错",
        "改成中景或侧面，让手不在画面中心；产品展示可以写「产品放在桌上，镜头缓慢推近」",
    ),
    "text": (
        "画面里要出现文字，AI 生成的文字大多是乱码",
        "画面描述里去掉文字，需要的字用「叠加文字」加上，清晰也能改",
    ),
    "people": (
        "画面里有多人互动，容易出现肢体粘连、脸互相串",
        "尽量只拍一个人；互动拆成两个镜头分别拍，或改成背影、远景",
    ),
    "motion": (
        "画面里有复杂或快速的动作，容易变形、穿模",
        "动作放慢、幅度改小，例如「慢慢转身」「缓步走向镜头」；或者只拍动作前后的一个瞬间",
    ),
    "camera": (
        "运镜太快，画面容易撕裂、主体变形",
        "改成缓慢推近、缓慢横移或固定镜头",
    ),
    "sequence": (
        "一个镜头里写了好几个连续动作，模型常常做不完或顺序错乱",
        "一个镜头只写一个动作；多出来的动作拆到后面的镜头",
    ),
    "duplicate": (
        "画面描述和上一个镜头完全一样，生成出来的两个画面会很像",
        "改成和这句配音对应的画面，例如换个角度、景别或动作",
    ),
    "too_long": (
        "配音约 {seconds} 秒，超过这个模型一次能生成的 {max} 秒，要分 {parts} 段续生成，费用约 {parts} 倍，衔接处可能不自然",
        "把这句文案拆成两个镜头，或删减到 {max} 秒以内",
    ),
}


def _matches(pattern: re.Pattern, text: str) -> bool:
    for found in pattern.finditer(text):
        if not _NEGATION.search(text[max(0, found.start() - 12) : found.start()]):
            return True
    return False


def prompt_risks(text: str) -> list[Risk]:
    """画面描述里的高风险内容（只看文字）。"""
    text = text or ""
    risks = [Risk(code) for code, patterns in _COMPILED.items() if any(_matches(p, text) for p in patterns)]
    if len(_SEQUENCE.findall(text)) >= MAX_SEQUENCE:
        risks.append(Risk("sequence"))
    return [_fill(risk) for risk in risks]


def parts_needed(seconds: float, longest: float) -> int:
    """配音 ``seconds`` 秒、模型一段最长 ``longest`` 秒时要分几段生成。"""
    if longest <= 0 or seconds <= longest + EXTEND_MARGIN:
        return 1
    return int(-(-seconds // longest))


def duration_risk(seconds: float, longest: float | None) -> Risk | None:
    """配音超过模型单次生成上限时的提醒。"""
    if not longest or parts_needed(seconds, longest) <= 1:
        return None
    return _fill(
        Risk(
            "too_long",
            {"seconds": round(seconds, 1), "max": round(longest, 1), "parts": parts_needed(seconds, longest)},
        )
    )


def shot_risks(
    project: Project, shot: Shot, *, prompt: str | None = None, longest: float | None = None
) -> list[Risk]:
    """一个 AI 镜头生成前的检查结果。``prompt`` 是还没保存的画面描述；``longest`` 是模型一段的上限。"""
    from shotwright.services.durations import estimated_shot_seconds

    visual = shot.visual
    if visual.mode not in ("ai", "image"):
        return []
    text = prompt if prompt is not None else f"{visual.prompt}\n{visual.image_prompt}"
    risks = prompt_risks(text)
    if _same_as_previous(project, shot, prompt if prompt is not None else visual.prompt):
        risks.append(_fill(Risk("duplicate")))
    if visual.mode == "ai":
        long = duration_risk(estimated_shot_seconds(project, shot), longest)
        if long is not None:
            risks.append(long)
    return risks


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _same_as_previous(project: Project, shot: Shot, prompt: str) -> bool:
    """和上一个 AI 镜头的画面描述完全相同（例如按文案拆出来的后半段还没改描述）。"""
    index = next((i for i, item in enumerate(project.shots) if item.id == shot.id), 0)
    if index == 0 or not _normalize(prompt):
        return False
    previous = project.shots[index - 1]
    return previous.visual.mode in ("ai", "image") and _normalize(previous.visual.prompt) == _normalize(
        prompt
    )


def _fill(risk: Risk) -> Risk:
    message, advice = MESSAGES[risk.code]
    risk.message = message.format(**risk.params)
    risk.advice = advice.format(**risk.params)
    return risk


def to_dicts(risks: list[Risk]) -> list[dict]:
    return [asdict(risk) for risk in risks]
