"""补救推荐：检查出问题时，先给出最省钱的修法，不急着重新生成。

生成的画面出了问题，重新生成要再花一次钱，而且不一定比这回好。多数问题其实能原地修好：

- 素材太短：调入出点、改成慢放或定格、换一段更长的素材（都不花钱）；
- 分辨率不够：换更高分辨率的素材，或按成片档渲染（不花钱）；
- 画面描述里有高风险内容：先按建议改描述再生成（改写本身不花钱）；
- 配音超长：拆成两个镜头（不花钱）；
- 产品走样、手指畸形：先看别的候选里有没有更好的，再考虑改画面或重做。

每条补救带 ``cost``：
- ``free`` 不用生成，直接改设置；
- ``cheap`` 改画面或换来源，比整条重新生成便宜；
- ``paid`` 重新生成。

界面按 ``free → cheap → paid`` 的顺序显示，把不花钱的放前面。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

Cost = str
"""free：不花钱；cheap：比重新生成便宜；paid：重新生成。"""

_COST_ORDER = {"free": 0, "cheap": 1, "paid": 2}


@dataclass
class Remedy:
    action: str
    """补救方式（界面据此显示按钮）：trim / fill / swap / rerender / edit_prompt / split / recolor。"""
    cost: Cost = "free"
    params: dict = field(default_factory=dict)
    """界面按 action 和这些参数显示本地化的说明。"""
    message: str = ""
    """中文说明（命令行和日志用）。"""


# 每个检查项对应的补救方式，按省钱的排在前面。
_REMEDIES: dict[str, list[tuple[str, Cost, str]]] = {
    "short_footage": [
        (
            "fill",
            "free",
            "先改成慢放或定格补足（免费）：慢放到 0.8 倍再加定格，画面还是原来的镜头",
        ),
        ("trim", "free", "或调整这段素材的入出点，把被剪掉的片段放回来，通常能多出几秒"),
        ("swap", "free", "换一段更长的素材（素材库检索和上传都不花钱）"),
        ("paid", "paid", "还可以重新生成一段更长的 AI 视频，但这要再花一次钱"),
    ],
    "low_resolution": [
        ("swap", "free", "换一段更高分辨率的素材（素材库检索不花钱）"),
        ("rerender", "free", "或改用成片档渲染，草稿档的分辨率本来就低"),
    ],
    "no_visual": [
        ("swap", "free", "先上传自己的图片或从素材库选一段（不花钱），看看效果够不够"),
        ("paid", "paid", "满意了再决定要不要花钱生成 AI 画面"),
    ],
    "subject_in_stock": [
        ("swap", "free", "先在素材库的其他候选里找更接近的，或在「更多」里上传自己的照片"),
        ("paid", "paid", "实在找不到再改成 AI 视频，让模型照着主体参考图生成"),
    ],
    "subject_no_image": [
        ("upload", "free", "在全片设定里给这个主体上传一张照片（免费），比靠文字描述准得多"),
        ("paid", "paid", "也可以让 AI 生成一张定妆图，之后各镜头都参照它"),
    ],
    "no_prompt": [
        ("edit_prompt", "free", "用「提示词扩写」把画面描述补全（只调用文字模型，不生成画面）"),
    ],
    "overlay_subtitle": [
        ("move", "free", "把叠加文字挪到顶部或中间，避开字幕所在的位置（不花钱）"),
    ],
    "overlay_overlap": [
        ("move", "free", "把它们的时间错开，或换到不同位置（不花钱）"),
    ],
    "platform_aspect": [
        ("rerender", "free", "换一个和平台匹配的画面比例再渲染（不花钱）"),
    ],
    "no_look": [
        ("look", "free", "点「补全全片设定」，把人物和产品的外观定下来（只调用文字模型）"),
    ],
    "over_budget": [
        ("budget", "free", "先在渲染面板调到草稿档，或挑几个关键镜头先生成（不花钱）"),
        ("trim_scope", "free", "也可以先只生成最需要的几个镜头，其余的用素材库"),
    ],
}

# 生成前检查（precheck）的问题：改动描述或拆镜头，都不用重新花钱。
_RISK_REMEDIES: dict[str, str] = {
    "hands": "改成中景或侧面避开手部特写（改描述不花钱），实在需要手部就考虑用素材库或自己拍",
    "text": "把文字从画面描述里去掉，改用「叠加文字」加上（改描述和加字都不花钱）",
    "people": "只拍一个人，或拆成两个镜头分别拍（拆镜头不花钱）",
    "motion": "把动作改慢、改小，或只拍动作前后的一个瞬间（改描述不花钱）",
    "camera": "改成缓慢推近或固定镜头（改描述不花钱）",
    "sequence": "一个镜头只留一个动作，多出来的拆到后面的镜头（拆镜头不花钱）",
    "duplicate": "改成和这句配音对应的画面，换个角度或景别（改描述不花钱）",
    "too_long": "按句子把这句文案拆成两个镜头（拆镜头不花钱，比续生成省一半费用）",
}


def for_issue(code: str) -> list[Remedy]:
    """一个检查项（checkup 的 code，或 ``risk_xxx``）的补救方式，按省钱排序。"""
    if code.startswith("risk_"):
        risk = code[5:]
        advice = _RISK_REMEDIES.get(risk)
        if advice is None:
            return []
        action = "split" if risk in ("too_long", "sequence") else "edit_prompt"
        return [Remedy(action, "free", {"risk": risk}, advice)]
    items = _REMEDIES.get(code)
    if not items:
        return []
    return [Remedy(action, cost, {"code": code}, message) for action, cost, message in items]


def best(code: str) -> Remedy | None:
    """最省钱的那一条，也就是该先试的。"""
    found = for_issue(code)
    return min(found, key=lambda item: _COST_ORDER.get(item.cost, 9)) if found else None


def to_dicts(issues: list) -> list[dict]:
    """给检查结果补上补救建议：每条 issue 多一个 ``remedies`` 列表。"""
    return [{**asdict(issue), "remedies": [asdict(r) for r in for_issue(issue.code)]} for issue in issues]
