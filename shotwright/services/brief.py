"""新建视频的引导流程：一句话想法 → 按模板整理的诉求 → 可编辑的脚本 → 分镜；以及上传素材的归位。

* :func:`normalize_brief`：把用户随手写的想法按视频类型的诉求模板整理成完整诉求，
  不编造事实，不知道的地方标成【待补充：…】让用户填；
* :func:`write_script`：按诉求写口播脚本，每段一个镜头，交给用户修改；
* :func:`apply_materials`：分镜写好后，把用户上传的素材放到该放的地方
  （产品图 / 人物照片进全片设定的主体图片，Logo / 风格图进主体库，画面素材进镜头，音频做背景音乐）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shotwright.core.models import (
    MAX_SUBJECT_IMAGES,
    VIDEO_TYPE_NAMES,
    Project,
    ReferenceAsset,
    check_subject,
)
from shotwright.providers.base import LLMProvider, ProviderError
from shotwright.providers.tts.silent import estimate_seconds
from shotwright.services.storyboard import _extract_json

MaterialRole = Literal["product", "person", "logo", "style", "footage", "bgm"]
"""素材用途：产品图、人物照片、Logo、风格参考、画面素材（直接当镜头画面）、背景音乐。"""

ROLE_NAMES: dict[str, str] = {
    "product": "产品图",
    "person": "人物照片",
    "logo": "Logo",
    "style": "风格参考",
    "footage": "画面素材",
    "bgm": "背景音乐",
}
KIND_NAMES = {"image": "图片", "video": "视频", "audio": "音频"}


@dataclass
class Material:
    """一份用户素材的说明（写诉求、脚本、分镜时交给大模型）。"""

    kind: str
    role: str
    name: str = ""
    note: str = ""
    asset_id: str | None = None

    def describe(self, code: str) -> str:
        parts = [code, KIND_NAMES.get(self.kind, self.kind), ROLE_NAMES.get(self.role, self.role)]
        if self.name:
            parts.append(f"文件名 {self.name[:60]}")
        if self.note.strip():
            parts.append(f"说明：{self.note.strip()[:200]}")
        return "，".join(parts)


def describe_materials(materials: list[Material]) -> list[str]:
    return [item.describe(f"M{index}") for index, item in enumerate(materials, start=1)]


NORMALIZE_PROMPT = """你是短视频策划。用户用几句话说了想做什么视频，请按「{kind}」的诉求模板把它整理成一份完整、具体的视频诉求，用{language}写。

要求：
- 按模板的字段顺序逐项写，每项一行，保留「字段：」开头；模板里【】是要填的内容。
- 用户说过的事实（产品名称、外观、卖点、价格、平台、人群等）全部保留，不要改写成别的意思。
- 用户没说、但可以合理推断的（例如节奏、配音风格、开头怎么抓人、画面风格），按这类视频的常见做法补全，写得具体。
- 用户没说、又不能编造的事实（价格、优惠、具体参数、品牌背书、功效数据），写成【待补充：xxx】，不要编造。
- 产品和人物的外观要写具体（颜色、材质、形状、尺寸、服装、发型），这是之后保证每个镜头一致的关键。
- 用户上传了素材的，在相应字段里提到它（例如「产品外观以上传的产品图为准」「开头用上传的画面素材 M2」）。
- 不写品牌名，改写成外观特征；不要求画面里出现文字。
- 只输出整理好的诉求正文，不要标题、解释或 Markdown。

模板：
{template}"""

SCRIPT_PROMPT = """你是短视频编导。根据视频诉求写一份口播脚本，用{language}写。
- 口语化，开头 1–2 句要抓人，结尾有明确的引导或落点。
- {length_rule}
- 每段对应一个镜头，1–2 句话，朗读 3–8 秒；按诉求里的结构和要点安排段落顺序。
- 诉求里标着【待补充】的信息不要编造具体数字，用不依赖这个信息的说法。
- 需要停一下的地方可以写 [停顿]，要重读的词用星号包住，例如 *一键*；每段最多一两处。
只输出 JSON：{{"title": "视频标题（不超过 20 个字）", "lines": ["第一段文案", "第二段文案"]}}"""


def _context(
    video_type: str | None, aspect: str | None, duration: float | None, materials: list[Material]
) -> list[str]:
    lines = []
    if video_type in VIDEO_TYPE_NAMES:
        lines.append(f"视频类型：{VIDEO_TYPE_NAMES[video_type]}")
    if aspect:
        lines.append(f"画面比例：{aspect}")
    if duration:
        lines.append(f"目标时长：约 {duration:g} 秒")
    if materials:
        lines.append("用户上传的素材：")
        lines += [f"- {item}" for item in describe_materials(materials)]
    return lines


def normalize_brief(
    llm: LLMProvider,
    idea: str,
    *,
    template: str,
    video_type: str | None = None,
    language: str = "简体中文",
    aspect: str | None = None,
    duration: float | None = None,
    materials: list[Material] | None = None,
) -> str:
    """把用户的想法按诉求模板整理成完整诉求（纯文本，长度不超过诉求上限）。"""
    if not idea.strip():
        raise ProviderError("请先说说想做什么视频")
    kind = VIDEO_TYPE_NAMES.get(video_type or "", "短视频")
    system = NORMALIZE_PROMPT.format(kind=kind, language=language, template=template.strip()[:4000])
    user = "\n".join(
        [*_context(video_type, aspect, duration, materials or []), f"用户的想法：\n{idea.strip()[:4000]}"]
    )
    reply = llm.complete([{"role": "system", "content": system}, {"role": "user", "content": user}]).strip()
    if reply.startswith("```"):
        reply = reply.strip("`").split("\n", 1)[-1].strip()
    if not reply:
        raise ProviderError("大模型没有返回诉求")
    return _fit(reply)


def _fit(text: str) -> str:
    """截到诉求上限以内（按行截，不截断半句）。"""
    try:
        return check_subject(text)
    except ValueError:
        kept: list[str] = []
        for line in text.splitlines():
            candidate = "\n".join([*kept, line])
            try:
                check_subject(candidate)
            except ValueError:
                break
            kept.append(line)
        return "\n".join(kept)


def _length_rule(duration: float | None) -> str:
    if not duration:
        return "整条视频控制在 30–60 秒。"
    low = max(1, round(duration / 8))
    high = max(low + 1, round(duration / 4))
    return f"整条视频朗读约 {duration:g} 秒，大约 {low}–{high} 段。"


def write_script(
    llm: LLMProvider,
    brief: str,
    *,
    video_type: str | None = None,
    language: str = "简体中文",
    aspect: str | None = None,
    duration: float | None = None,
    materials: list[Material] | None = None,
) -> tuple[str, list[str]]:
    """按诉求写口播脚本，返回（标题, 段落列表）。"""
    if not brief.strip():
        raise ProviderError("请先填写视频诉求")
    system = SCRIPT_PROMPT.format(language=language, length_rule=_length_rule(duration))
    user = "\n".join(
        [*_context(video_type, aspect, duration, materials or []), f"视频诉求：\n{brief.strip()}"]
    )
    data = _extract_json(
        llm.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}], json_mode=True
        )
    )
    lines = [str(item).strip()[:500] for item in data.get("lines") or [] if str(item).strip()][:60]
    if not lines:
        raise ProviderError("大模型没有返回脚本")
    return str(data.get("title") or "").strip()[:60], lines


def script_seconds(lines: list[str]) -> float:
    """脚本大约要读多久（和渲染时估算镜头时长的算法一致）。"""
    return round(sum(estimate_seconds(line) for line in lines if line.strip()), 1)


def apply_materials(
    project: Project,
    materials: list[Material],
    footage: dict[int, str] | None = None,
    *,
    photos_as_footage: bool = False,
) -> None:
    """把上传的素材放到该放的地方。``footage`` 是大模型建议的「镜头序号 → 素材编号」。

    - 产品图 / 人物照片：加到全片设定里第一个同类主体的图片；没有这类主体时进主体库。
    - Logo / 风格参考：进主体库。
    - 画面素材（图片、视频）：先按大模型的建议放进对应镜头，其余按顺序放进还没有画面的镜头。
    - 背景音乐：设为背景音乐（只用第一首）。
    """
    codes = {f"M{index}": item for index, item in enumerate(materials, start=1)}
    placed: set[str] = set()

    for item in materials:
        asset = project.assets.get(item.asset_id or "")
        if asset is None:
            continue
        if item.role in ("product", "person") and asset.kind == "image":
            subject = next((s for s in project.look.subjects if s.kind == item.role), None)
            if subject is not None and len(subject.images) < MAX_SUBJECT_IMAGES:
                if asset.id not in subject.images:
                    subject.images.append(asset.id)
                continue
            if not any(ref.asset_id == asset.id for ref in project.references):
                project.references.append(
                    ReferenceAsset(asset_id=asset.id, role=item.role, label=(item.note or item.name)[:100])
                )
        elif item.role in ("logo", "style") and asset.kind == "image":
            if not any(ref.asset_id == asset.id for ref in project.references):
                project.references.append(
                    ReferenceAsset(asset_id=asset.id, role=item.role, label=(item.note or item.name)[:100])
                )
        elif item.role == "bgm" and asset.kind == "audio":
            if project.bgm.asset_id is None:
                project.bgm.asset_id = asset.id

    def put(shot_index: int, asset_id: str) -> None:
        shot = project.shots[shot_index]
        visual = shot.visual
        visual.mode = "upload"
        if asset_id not in visual.candidates:
            visual.candidates.append(asset_id)
        visual.selected = asset_id
        visual.sequence = []
        placed.add(asset_id)

    for shot_index, code in sorted((footage or {}).items()):
        item = codes.get(code)
        asset = project.assets.get(item.asset_id or "") if item else None
        if (
            item is not None
            and asset is not None
            and item.role == "footage"
            and asset.kind in ("image", "video")
            and 0 <= shot_index < len(project.shots)
            and asset.id not in placed
        ):
            put(shot_index, asset.id)

    # 没有大模型写画面时，产品图、人物照片也直接当画面用（慢推镜头），免得整条视频都是纯色底。
    roles = ("footage", "product", "person") if photos_as_footage else ("footage",)
    remaining = [
        item.asset_id
        for item in materials
        if item.role in roles
        and item.asset_id in project.assets
        and project.assets[item.asset_id].kind in ("image", "video")
        and item.asset_id not in placed
    ]
    free = [
        index
        for index, shot in enumerate(project.shots)
        if shot.visual.mode != "upload" or not shot.visual.selected
    ]
    for shot_index, asset_id in zip(free, remaining, strict=False):
        put(shot_index, asset_id)  # type: ignore[arg-type]
