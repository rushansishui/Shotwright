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
from typing import Literal

from shotwright.core.models import Project, Shot

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
        "person": "人物参考：保持人物的五官、发型、肤色与参考图一致",
        "logo": "Logo 参考：保持 Logo 的图形与颜色不变，清晰可辨",
        "style": "风格参考：沿用参考图的色调、光线与画面质感",
    },
    "en": {
        "product": "Product reference: keep the product's shape, color, material and logo position exactly as in the "
        "reference image; do not redesign it",
        "person": "Person reference: keep the face, hairstyle and skin tone the same as the reference image",
        "logo": "Logo reference: keep the logo's shape and colors unchanged and legible",
        "style": "Style reference: follow the reference image's color palette, lighting and texture",
    },
}


def used_codes(project: Project, text: str) -> list[str]:
    """画面描述里提到的主体代号（按全片设定的顺序）。代号按完整单词匹配，大小写敏感。"""
    found = []
    for subject in project.look.subjects:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(subject.code)}(?![A-Za-z0-9_])", text):
            found.append(subject.code)
    return found


def reference_lines(project: Project, lang: PromptLang) -> list[str]:
    """主体库里启用的参考图对应的要求（与参考图一起交给模型）。"""
    lines = []
    for ref in project.references:
        if not ref.enabled:
            continue
        asset = project.assets.get(ref.asset_id)
        if asset is None or asset.kind != "image":
            continue
        hint = ROLE_HINTS[lang].get(ref.role, "")
        if hint:
            lines.append(f"- {hint}" + (f" ({ref.label})" if ref.label else ""))
    return lines


def compose(project: Project, shot: Shot, *, target: Target = "video", prompt: str | None = None) -> str:
    """拼出交给生成模型的完整提示词。

    顺序：镜头画面描述 → 运镜 → 衔接 → 主体外观 → 场景与风格 → 参考图要求 → 禁止项。
    把镜头本身放在最前面，模型最重视开头的内容。
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
    subjects = [s for s in look.subjects if s.code in codes and s.appearance.strip()]
    if subjects:
        lines.append(words["subjects"])
        lines += [
            words["subject"].format(code=s.code, name=s.name or s.kind, appearance=s.appearance.strip())
            for s in subjects
        ]
    if look.scene.strip():
        lines.append(words["scene"].format(look.scene.strip()))
    if look.style.strip():
        lines.append(words["style"].format(look.style.strip()))
    refs = reference_lines(project, lang)
    if refs:
        lines.append(words["refs"])
        lines += refs
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
