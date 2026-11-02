"""每家生视频模型单独的一套提示词写法。

同一个镜头交给不同模型，写法不该一样：

- **Seedance / 通义万相** 的提示词预算大（5000 字），能接受完整的全片设定 + 主体外观 +
  参考图要求，长描述反而更稳定；
- **可灵** 的预算 2500 字，且对一整段长描述容易抓不到重点，更适合「主体 + 动作 + 运镜」
  写清楚，全片风格能省就省；
- **MiniMax 海螺** 的预算只有 2000 字，而且一个镜头里写多个连续动作时明显更容易乱，
  要强调「一个镜头一个动作」；
- **fal.ai** 走英文，预算 4000 字。

这里声明每个模型的预算和写法偏好。拼提示词时（``prompting.compose``）按风格决定
保留哪些段落：超预算时先丢风格和场景描述，主体外观、参考图要求和禁止项始终保留——
这几段丢了产品就会走样。

预算都低于各 Provider 自己的 ``MAX_PROMPT``（那是硬截断，会从中间切掉句子），
本模块要做的是在截断之前把提示词整理到预算内，见 ``tests/test_model_rules.py``。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelStyle:
    """一个模型的提示词写法。"""

    id: str
    label: str
    budget: int
    """完整提示词的字符预算。超过时按 :data:`DROP_ORDER` 依次丢掉次要段落。"""
    max_shot_prompt: int
    """单个镜头画面描述的建议上限（分镜生成时按它提醒）。"""
    one_action: bool = False
    """是否强调「一个镜头只写一个动作」。"""
    notes: str = ""


STYLES: dict[str, ModelStyle] = {
    "seedance": ModelStyle(
        "seedance",
        "火山方舟 Seedance",
        budget=5000,
        max_shot_prompt=600,
        notes="预算大，接受完整的长描述和参考图要求；主体外观写得越细越稳。",
    ),
    "wan": ModelStyle(
        "wan",
        "通义万相",
        budget=5000,
        max_shot_prompt=600,
        notes="预算大，支持 10 张参考图；长描述可用，写清楚主体和动作顺序。",
    ),
    "kling": ModelStyle(
        "kling",
        "可灵 Kling",
        budget=2500,
        max_shot_prompt=400,
        notes="预算中等，长描述容易抓不到重点；全片设定先压缩，突出主体、动作和运镜。",
    ),
    "minimax": ModelStyle(
        "minimax",
        "MiniMax 海螺",
        budget=2000,
        max_shot_prompt=300,
        one_action=True,
        notes="预算最小，一个镜头写多个动作时容易乱；只保留最必要的段落，强调一个动作。",
    ),
    "fal": ModelStyle(
        "fal",
        "fal.ai",
        budget=4000,
        max_shot_prompt=450,
        notes="英文提示词；聚合平台上各家模型差异大，按通用写法处理。",
    ),
}

DEFAULT = ModelStyle(
    "default",
    "通用",
    budget=5000,
    max_shot_prompt=600,
    notes="没有单独规则的模型按通用写法处理：预算按 Provider 声明的上限。",
)

DROP_ORDER: tuple[str, ...] = ("style", "scene", "camera", "link")
"""超预算时依次丢掉的段落（从最先丢开始）。

``subjects``（主体外观）、``refs``（参考图要求）和 ``avoid``（禁止项）永远保留：
这几段一丢，产品和人物在各镜头就对不上。
"""


def style_for(provider_id: str | None) -> ModelStyle:
    """按 Provider id 取写法；没单独声明时用通用写法。"""
    return STYLES.get((provider_id or "").strip().lower(), DEFAULT)


def fit(sections: list[tuple[str, list[str]]], *, budget: int, sep: str = "\n") -> str:
    """把各段落拼成提示词；超过 ``budget`` 时按 :data:`DROP_ORDER` 丢掉次要段落。

    丢完还超时按行从后往前删（最后一行是禁止项，留着），仍超才硬截断。
    """
    kept = list(sections)
    text = _join(kept, sep)
    for key in DROP_ORDER:
        if len(text) <= budget:
            break
        kept = [item for item in kept if item[0] != key]
        text = _join(kept, sep)
    # 还超：从后往前删次要段落里的行，禁止项（最后一段）不动。
    while len(text) > budget and len(kept) > 1:
        key, lines = kept[-2]
        if len(lines) <= 1:
            break
        kept[-2] = (key, lines[:-1])
        text = _join(kept, sep)
    if len(text) > budget:
        text = _truncate(text, budget)
    return text


def _join(sections: list[tuple[str, list[str]]], sep: str) -> str:
    return sep.join(line for _, lines in sections for line in lines if line)


def _truncate(text: str, budget: int) -> str:
    """硬截断时尽量在句末断开，不留半句。"""
    cut = text[:budget]
    for mark in ("。", "；", ". ", "; ", "\n"):
        at = cut.rfind(mark)
        if at > budget * 0.6:
            return cut[: at + len(mark)].rstrip()
    return cut.rstrip()


def hint(style: ModelStyle, lang: str) -> str:
    """拼在提示词末尾的一句模型专属要求；没有额外要求时返回空字符串。"""
    if not style.one_action:
        return ""
    return {
        "zh": "只表现一个连贯的动作，不要在一个镜头里切换场景或先后做几件事。",
        "en": "Show a single continuous action; do not change location or chain several actions in this shot.",
    }.get(lang, "")


def too_long(style: ModelStyle, text: str) -> bool:
    """单个镜头的画面描述是不是超过了这个模型的建议长度（分镜自检用）。"""
    return len((text or "").strip()) > style.max_shot_prompt
