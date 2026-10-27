"""把一个镜头的画面描述拼成交给生成模型的完整提示词。

镜头里只存「这个镜头本身」的描述；全片设定（主体外观、场景、风格、禁止项）、
主体库参考要求、运镜和衔接说明都在生成时按固定格式拼上。这样改一处全片设定，
所有镜头下次生成时都会跟着变，各镜头也始终用同一套外观描述。

提示词用项目语言写（中文项目用中文，其他语言用英文），界面上看到的就是交给
模型的内容；模型不支持这种语言时，在提交前再翻译（见 :func:`for_provider`）。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Literal

from shotwright.core.models import LookSubject, Project, Shot

log = logging.getLogger("shotwright.prompting")

PromptLang = Literal["zh", "en"]
Target = Literal["video", "image"]


def prompt_language(project: Project) -> PromptLang:
    """画面描述用什么语言写：中文项目用中文，其他语言统一用英文（生成模型对英文支持最好）。"""
    return "zh" if project.language.lower().startswith("zh") else "en"


CAMERA_PHRASES: dict[str, dict[str, str]] = {
    "zh": {
        "static": "固定机位，画面稳定",
        "push_in": "镜头缓慢推近主体",
        "pull_out": "镜头缓慢拉远，展现全景",
        "pan": "镜头平稳横摇",
        "truck": "镜头侧向跟拍主体移动",
        "orbit": "镜头绕主体缓慢环绕",
        "crane": "升降镜头缓缓升起",
        "handheld": "手持跟拍，轻微自然晃动",
    },
    "en": {
        "static": "Locked-off static camera",
        "push_in": "Slow push-in toward the subject",
        "pull_out": "Slow pull-back revealing the whole scene",
        "pan": "Smooth horizontal pan",
        "truck": "Lateral tracking shot moving with the subject",
        "orbit": "Slow orbit around the subject",
        "crane": "Crane shot rising slowly",
        "handheld": "Handheld camera with subtle natural shake",
    },
}

CONTINUE: dict[str, str] = {
    "zh": "接着上一段画面继续，动作自然延续，不要重新开始或切换场景。",
    "en": "Continue seamlessly from the previous clip; the action carries on without restarting or cutting away.",
}

_PHRASES: dict[str, dict[str, str]] = {
    "zh": {
        "style": "视觉风格：{}",
        "scene": "场景：{}",
        "subjects": "主体（各镜头保持一致）：",
        "subject": "- {code}（{name}）：{appearance}",
        "camera": "运镜：{}",
        "link": "衔接：{}",
        "avoid": "不要出现：{}",
        "no_text": "画面中不出现任何文字、字幕、水印或品牌名称文字。",
        "still": "这是一张静态画面，构图完整，主体清晰。",
        "refs": "参考图要求：",
        "sep": "；",
    },
    "en": {
        "style": "Visual style: {}",
        "scene": "Setting: {}",
        "subjects": "Subjects (keep consistent across shots):",
        "subject": "- {code} ({name}): {appearance}",
        "camera": "Camera: {}",
        "link": "Continuity: {}",
        "avoid": "Avoid: {}",
        "no_text": "No on-screen text, captions, watermarks or written brand names.",
        "still": "A single still frame with a complete composition and a clear subject.",
        "refs": "Reference images:",
        "sep": "; ",
    },
}

ROLE_HINTS: dict[str, dict[str, str]] = {
    "zh": {
        "product": "产品外观参考：保持商品形状、颜色、材质、Logo 位置与参考图一致，不要重新设计商品",
        "person": "人物参考：只参考五官、发型、肤色；衣服按画面描述来穿，不要照搬参考图里的衣服",
        "logo": "Logo 参考：保持 Logo 的图形与颜色不变，清晰可辨",
        "style": "风格参考：沿用参考图的色调、光线与画面质感",
    },
    "en": {
        "product": "Product reference: keep the product's shape, color, material and logo position exactly as in the "
        "reference image; do not redesign it",
        "person": "Person reference: follow only the face, hairstyle and skin tone; dress the person as the shot "
        "describes rather than copying the clothes in the reference",
        "logo": "Logo reference: keep the logo's shape and colors unchanged and legible",
        "style": "Style reference: follow the reference image's color palette, lighting and texture",
    },
}


REF_LABEL = {"zh": "参考图 {images}", "en": "Reference image {images}"}
THIS_SHOT = {"zh": "；这个镜头里是「{label}」款", "en": "; in this shot it is the {label} version"}
THESE_SHOT = {"zh": "；这个镜头里出现「{label}」这几款", "en": "; this shot shows the {label} versions"}
"""参考图编号：和交给模型的顺序一致（按主体顺序，每个主体的图片按上传顺序）。"""


def used_codes(project: Project, text: str) -> list[str]:
    """画面描述里提到的主体代号（按全片设定的顺序）。代号按完整单词匹配，大小写敏感。"""
    found = []
    for subject in project.look.subjects:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(subject.code)}(?![A-Za-z0-9_])", text):
            found.append(subject.code)
    return found


MAX_REFERENCES = 4
"""一个镜头默认最多带几张参考图；生成服务能接受更多时（Seedance 2.x 9 张）由调用方传 ``limit``。"""


@dataclass
class ShotRef:
    """镜头用到的一张参考图：属于哪个主体、是哪一款（颜色 / 款式说明）。"""

    asset_id: str
    subject: LookSubject | None = None
    label: str = ""
    role: str = ""
    """主体库里的参考图的角色（product / person）；全片设定里的主体图片为空。"""


def _mentioned_variants(subject: LookSubject, text: str) -> list[str]:
    """画面描述里点名的几款（按图片标注的颜色 / 款式匹配，「浅粉色」也匹配「浅粉」）。"""
    found = []
    for image in subject.images:
        label = subject.image_labels.get(image, "").strip()
        if not label:
            continue
        short = label[:-1] if len(label) > 2 and label.endswith(("色", "款")) else label
        if label in text or short in text:
            found.append(image)
    return found


def shot_references(
    project: Project, shot: Shot, *, target: Target = "video", limit: int = MAX_REFERENCES
) -> list[ShotRef]:
    """这个镜头交给生成模型的参考图，按顺序（第 1 张就是提示词里的「参考图 1」）。

    镜头自己选了参考图（``ShotVisual.refs``）就用选的；没选时，画面里提到的每个主体只用主图
    （第一张）——同一主体的几张图常是不同颜色或款式，一起交给模型它会混着来、自己挑颜色。
    最后补上主体库里启用的产品、人物参考图。最多 :data:`MAX_REFERENCES` 张。
    """
    look = project.look
    owner = {image: subject for subject in look.subjects for image in subject.images}
    result: list[ShotRef] = []

    def add(asset_id: str, subject: LookSubject | None, label: str = "", role: str = "") -> None:
        asset = project.assets.get(asset_id)
        if asset is None or asset.kind != "image" or any(item.asset_id == asset_id for item in result):
            return
        result.append(ShotRef(asset_id, subject, label, role))

    if shot.visual.refs:
        for asset_id in shot.visual.refs:
            subject = owner.get(asset_id)
            add(asset_id, subject, subject.image_labels.get(asset_id, "") if subject else "")
    else:
        text = (shot.visual.image_prompt or shot.visual.prompt) if target == "image" else shot.visual.prompt
        codes = used_codes(project, text)
        subjects = [s for s in look.subjects if s.code in codes] if codes else [s for s in look.subjects if s.kind != "place"]
        # 产品、物品排在人物前面：参考图放不下时，先保证商品的样子对。
        subjects.sort(key=lambda s: s.kind == "person")
        for subject in subjects:
            # 画面里点名了几种颜色（例如四个小朋友各穿一种颜色）就带上这几款，否则只带主图。
            images = _mentioned_variants(subject, text) or subject.images[:1]
            for image in images:
                add(image, subject, subject.image_labels.get(image, ""))
    for ref in project.references:
        if ref.enabled and ref.role in ("product", "person"):
            add(ref.asset_id, None, ref.label, ref.role)
    return result[:limit]


def reference_lines(project: Project, lang: PromptLang, refs: list[ShotRef]) -> list[str]:
    """参考图对应的要求（与参考图一起交给模型）：第几张是哪个主体、哪一款，颜色必须照着参考图。

    主体库里启用的 Logo、风格参考没有作为图片提交，只写一句要求。
    """
    lines = []
    numbers: dict[str, list[int]] = {}
    for number, ref in enumerate(refs, start=1):
        if ref.subject is not None and ref.subject.kind != "person":
            numbers.setdefault(ref.subject.code, []).append(number)
    for number, ref in enumerate(refs, start=1):
        label = REF_LABEL[lang].format(images=number)
        if ref.subject is not None:
            subject = ref.subject
            hint = ROLE_HINTS[lang]["person" if subject.kind == "person" else "product"]
            variant = VARIANT[lang].format(label=ref.label) if ref.label else ""
            lines.append(f"- {label} = {subject.code}{' ' + subject.name if subject.name else ''}{variant}: {hint}")
            own = numbers.get(subject.code, [])
            if subject.kind != "person" and own and own[-1] == number:
                # 同一主体的要求在它最后一张参考图后面写一次。
                if len(own) == 1:
                    lines.append("  " + COLOR_RULE[lang].format(code=subject.code, number=number, label=ref.label or "-"))
                else:
                    joined = "、".join(map(str, own)) if lang == "zh" else ", ".join(map(str, own))
                    lines.append("  " + MULTI_COLOR_RULE[lang].format(code=subject.code, numbers=joined))
                lines.append("  " + SHAPE_RULE[lang].format(code=subject.code))
        else:
            hint = ROLE_HINTS[lang].get(ref.role, "")
            lines.append(f"- {label}: {hint}" + (f" ({ref.label})" if ref.label else ""))
    for ref in project.references:
        if ref.enabled and ref.role in ("logo", "style"):
            asset = project.assets.get(ref.asset_id)
            hint = ROLE_HINTS[lang].get(ref.role, "")
            if asset is not None and asset.kind == "image" and hint:
                lines.append(f"- {hint}" + (f" ({ref.label})" if ref.label else ""))
    return lines


VARIANT = {"zh": "（{label}款）", "en": " ({label})"}
COLOR_RULE = {
    "zh": "{code} 的颜色、图案、Logo 必须和参考图 {number} 完全一致（{label}），不要按文字描述或自行发挥换成别的颜色，也不要出现其他颜色的款式。",
    "en": "{code} must match reference image {number} exactly in color, pattern and logo ({label}); do not change the color "
    "based on the text or invent one, and do not show other color versions.",
}
MULTI_COLOR_RULE = {
    "zh": "{code} 的每一款颜色、图案、Logo 都和对应的参考图（{numbers}）完全一致，只出现这几款，不要自行发挥别的颜色。",
    "en": "Each version of {code} must match its reference image ({numbers}) exactly in color, pattern and logo; show only "
    "these versions and do not invent other colors.",
}
SHAPE_RULE = {
    "zh": "{code} 的款式结构以参考图为准：领型、有没有帽子、门襟和拉链、口袋、绗缝或缝线走向、袖口下摆都和参考图一样，"
    "不要添加参考图里没有的部件（例如帽子、毛领、口袋），也不要去掉参考图里有的。",
    "en": "{code}'s construction follows the reference images: collar, hood or no hood, closure and zipper, pockets, "
    "quilting/seams, cuffs and hem as shown; do not add parts the reference does not have (such as a hood, fur trim or "
    "pockets) and do not remove ones it has.",
}


FRAME_REF = {
    "zh": "视频第一帧 = {name}的参考照片：{hint}",
    "en": "The first frame of the video is the reference photo of {name}: {hint}",
}
"""用首帧代替参考图时（服务不支持参考图）的说明：第一帧就是主体的照片，外观以它为准。"""


def frame_reference_line(project: Project, lang: PromptLang, ref: ShotRef) -> str:
    """首帧就是某个主体的照片时，在提示词里写明，模型才知道要照着它画。"""
    if ref.subject is not None:
        subject = ref.subject
        name = f"{subject.code}{' ' + subject.name if subject.name else ''}"
        hint = ROLE_HINTS[lang]["person" if subject.kind == "person" else "product"]
    else:
        name = ref.label or ref.role
        hint = ROLE_HINTS[lang].get(ref.role, ROLE_HINTS[lang]["product"])
    return FRAME_REF[lang].format(name=name, hint=hint)


def compose(
    project: Project,
    shot: Shot,
    *,
    target: Target = "video",
    prompt: str | None = None,
    limit: int = MAX_REFERENCES,
    refs: list[ShotRef] | None = None,
    frame_ref: ShotRef | None = None,
) -> str:
    """拼出交给生成模型的完整提示词。

    顺序：镜头画面描述 → 运镜 → 衔接 → 主体外观 → 场景与风格 → 参考图要求 → 禁止项。
    把镜头本身放在最前面，模型最重视开头的内容。

    ``refs`` 给出时按它编号：调用方已经把读不出来的图片剔除了，编号必须和真正发出去的
    图片一致——否则「参考图 2」会指向另一张图，各镜头的人物、商品就对不上。
    传空列表表示这次不附带参考图，提示词里也就不写参考图要求。

    ``frame_ref`` 是作为首帧发出去的那张主体照片（服务不支持参考图时用它代替），
    提示词里写明第一帧就是它，模型才会照着画。
    """
    lang = prompt_language(project)
    words = _PHRASES[lang]
    look = project.look
    if prompt is None:
        prompt = (shot.visual.image_prompt or shot.visual.prompt) if target == "image" else shot.visual.prompt
    body = prompt.strip()
    lines = [body] if body else []
    if target == "video":
        camera = CAMERA_PHRASES[lang].get(shot.visual.camera)
        if camera:
            lines.append(words["camera"].format(camera))
        if shot.visual.link.strip():
            lines.append(words["link"].format(shot.visual.link.strip()))
    else:
        lines.append(words["still"])
    codes = used_codes(project, body) or [s.code for s in look.subjects if s.kind != "place"]
    shot_refs = refs if refs is not None else shot_references(project, shot, target=target, limit=limit)
    # 镜头选定了某一款（颜色 / 款式）时，外观描述后面注明，避免描述里列的几种颜色让模型自己挑。
    labels: dict[str, list[str]] = {}
    for ref in shot_refs:
        if ref.subject is not None and ref.label:
            labels.setdefault(ref.subject.code, []).append(ref.label)
    chosen = {
        code: (THIS_SHOT if len(items) == 1 else THESE_SHOT)[lang].format(label=("、" if lang == "zh" else ", ").join(items))
        for code, items in labels.items()
    }
    subjects = [s for s in look.subjects if s.code in codes and s.appearance.strip()]
    if subjects:
        lines.append(words["subjects"])
        lines += [
            words["subject"].format(code=s.code, name=s.name or s.kind, appearance=s.appearance.strip())
            + chosen.get(s.code, "")
            for s in subjects
        ]
    if look.scene.strip():
        lines.append(words["scene"].format(look.scene.strip()))
    if look.style.strip():
        lines.append(words["style"].format(look.style.strip()))
    refs = reference_lines(project, lang, shot_refs)
    if refs:
        lines.append(words["refs"])
        lines += refs
    if frame_ref is not None:
        lines.append(frame_reference_line(project, lang, frame_ref))
    avoid = look.avoid.strip()
    lines.append(words["avoid"].format(avoid) + words["sep"] + words["no_text"] if avoid else words["no_text"])
    return "\n".join(lines)


def needs_translation(project: Project, languages: tuple[str, ...]) -> bool:
    """模型支持的提示词语言里没有项目用的语言时，需要先翻译。"""
    return prompt_language(project) not in languages


_cache: dict[str, str] = {}


def translate(llm, text: str, *, to: PromptLang = "en") -> str:
    """用大模型把提示词翻译成模型支持的语言；结果按内容缓存在进程里，重复生成不再花钱翻译。"""
    key = hashlib.sha256(f"{to}\n{text}".encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    target = "English" if to == "en" else "简体中文"
    reply = llm.complete(
        [
            {
                "role": "system",
                "content": f"Translate the user's video-generation prompt into {target}. Keep the structure, line "
                "breaks and subject codes (like P1) unchanged. Output only the translation.",
            },
            {"role": "user", "content": text},
        ]
    ).strip()
    result = reply or text
    _cache[key] = result
    return result


def for_provider(settings, project: Project, text: str, languages: tuple[str, ...]) -> str:
    """按生成模型支持的语言准备提示词：支持就原样提交，不支持时用大模型翻译。

    没有配置大模型或翻译失败时原样提交，并记一条日志——多数模型也能看懂，只是效果可能差一些。
    """
    if not needs_translation(project, languages):
        return text
    from shotwright.providers import registry
    from shotwright.providers.base import ProviderError

    try:
        llm = registry.create("llm", None, settings)
        return translate(llm, text, to=languages[0] if languages[0] in ("zh", "en") else "en")  # type: ignore[arg-type]
    except (ProviderError, ValueError, KeyError) as exc:
        log.warning("提示词没有翻译成模型支持的语言，原样提交：%s", exc)
        return text


PORTRAIT = {
    "zh": {
        "person": "{name}的定妆照：人物正面半身照，中性自然的表情，{appearance}。纯浅灰色背景，柔和均匀的影棚光，面部和服装细节清晰。",
        "other": "{name}的标准展示图：主体完整、居中、正面，{appearance}。纯浅灰色背景，柔和均匀的影棚光，材质和细节清晰。",
        "tail": "这张图会作为之后所有镜头的参考，外观要和描述完全一致。画面中不出现任何文字、水印或品牌名称文字。",
    },
    "en": {
        "person": "Reference portrait of {name}: front-facing half-body shot, neutral natural expression, {appearance}. Plain light grey background, soft even studio light, clear detail on face and clothing.",
        "other": "Reference image of {name}: the whole subject, centered, front view, {appearance}. Plain light grey background, soft even studio light, clear material and detail.",
        "tail": "This image is the reference for every later shot, so match the description exactly. No on-screen text, watermarks or written brand names.",
    },
}


def subject_portrait_prompt(project: Project, code: str) -> str:
    """给全片设定里的主体生成定妆图（标准形象照）的提示词：纯色背景、完整正面，作为各镜头的参考图。"""
    lang = prompt_language(project)
    subject = next((s for s in project.look.subjects if s.code == code), None)
    if subject is None:
        raise KeyError(code)
    words = PORTRAIT[lang]
    template = words["person"] if subject.kind == "person" else words["other"]
    name = subject.name or subject.code
    appearance = subject.appearance.strip() or name
    return template.format(name=name, appearance=appearance) + "\n" + words["tail"]
