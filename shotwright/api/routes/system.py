"""基础信息：健康检查、可用服务、声音、语言、字体、交付平台。"""

from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from shotwright import __version__
from shotwright.api.context import ApiContext
from shotwright.core import languages, platforms
from shotwright.media import fonts as font_lib
from shotwright.providers import registry
from shotwright.providers.base import ProviderError
from shotwright.providers.tts.edge import VOICES


def register(api: APIRouter, ctx: ApiContext) -> None:
    settings = ctx.settings

    # ---- 基础 ---------------------------------------------------------------

    @api.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @api.get("/providers")
    def providers():
        result: dict[str, list[dict]] = {}
        for spec in registry.specs():
            result.setdefault(spec.kind, []).append(
                {
                    "id": spec.id,
                    "name": spec.name,
                    "name_en": spec.name_en or spec.name,
                    "paid": spec.paid,
                    "offline": spec.offline,
                    "docs_url": spec.docs_url,
                    "configured": registry.is_configured(spec, settings),
                    "default": settings.default_provider(spec.kind) == spec.id,
                    "fields": [
                        {
                            "key": f.key,
                            "label": f.label,
                            "required": f.required,
                            "secret": f.secret,
                            "default": f.default,
                            "help": f.help,
                        }
                        for f in spec.fields
                    ],
                    "required_any": [list(group) for group in spec.required_any],
                    "testable": spec.kind in ("llm", "video_gen", "asr"),
                    "capabilities": sorted(spec.capabilities),
                    "max_duration": spec.max_duration,
                    "has_audio": spec.has_audio,
                    "video_to_video": spec.video_to_video,
                    "max_ref_images": spec.max_ref_images,
                    "word_timestamps": spec.word_timestamps,
                }
            )
        return result

    @api.get("/voices")
    def voices(language: str | None = None, provider: str | None = None):
        """可选声音。带 provider 时返回该配音服务自己的声音列表。

        Edge 的声音按语言从语言目录取；其他服务的声音写在各自的 ProviderSpec 里。
        """
        chosen = provider or settings.default_provider("tts")
        if chosen and chosen != "edge":
            try:
                spec = registry.spec("tts", chosen)
            except ProviderError:
                spec = None
            if spec is not None and spec.voices:
                return [{"id": vid, "name": label, "language": None} for vid, label in spec.voices]
        if language:
            lang = languages.get(language)
            return [{"id": vid, "name": label, "language": lang.code} for vid, label in lang.voices]
        return [
            {"id": key, "name": name, "language": languages.voice_language(key)}
            for key, name in VOICES.items()
        ]

    @api.get("/languages")
    def list_languages():
        return [
            {
                "code": lang.code,
                "name": lang.name,
                "name_zh": lang.name_zh,
                "default_voice": lang.default_voice,
                "font": lang.font,
            }
            for lang in languages.LANGUAGES.values()
        ]

    # ---- 字体 ---------------------------------------------------------------

    @api.get("/fonts")
    def list_fonts():
        """按字体族分组的可用字体：内置字体和用户导入的字体。"""
        return font_lib.families(settings)

    @api.get("/fonts/{name}")
    def font_file(name: str):
        """字体文件本身，界面预览用。只返回字体库里的文件。"""
        face = font_lib.find(settings, name)
        if face is None:
            raise HTTPException(404, "找不到这个字体")
        media_type = "font/otf" if face.path.suffix.lower() == ".otf" else "font/ttf"
        return FileResponse(face.path, media_type=media_type, headers={"Cache-Control": "max-age=86400"})

    @api.post("/fonts", status_code=201)
    def import_font(file: UploadFile = File(...)):
        """导入自己的字体（.ttf / .otf），之后所有项目都能用。请只导入有使用授权的字体。"""
        filename = Path(file.filename or "font.ttf").name
        with tempfile.TemporaryDirectory(prefix="shotwright-font-") as temp:
            path = Path(temp) / ("upload" + Path(filename).suffix.lower()[:5])
            size = 0
            with path.open("wb") as handle:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > font_lib.MAX_FONT_BYTES:
                        raise HTTPException(413, "字体文件超过 40MB")
                    handle.write(chunk)
            try:
                face = font_lib.import_font(settings, path, filename)
            except font_lib.FontError as exc:
                raise HTTPException(422, str(exc)) from None
        return {"file": face.file, "family": face.family}

    @api.delete("/fonts/{name}", status_code=204)
    def delete_font(name: str):
        try:
            font_lib.delete_font(settings, name)
        except font_lib.FontError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.get("/platforms")
    def list_platforms():
        """交付平台规格：推荐画面比例和安全区（界面用来画安全区参考线）。"""
        return [
            {
                "id": p.id,
                "name": p.name,
                "name_en": p.name_en,
                "aspects": list(p.aspects),
                "safe": asdict(p.safe),
            }
            for p in platforms.PLATFORMS.values()
        ]
