"""HTTP API（/api/v2）与前端静态文件。

启动：``shotwright serve``，或 ``uvicorn shotwright.api.app:create_app --factory``。
"""

from __future__ import annotations

import logging
import mimetypes
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from shotwright import __version__, config_edit
from shotwright.api import errors
from shotwright.api.jobs import JobManager
from shotwright.api.schemas import (
    AddShot,
    AmbienceSearch,
    ApplyMaterials,
    AppSettings,
    BgmDownload,
    BgmSearch,
    CandidatesRequest,
    ChargeConsent,
    CreateProject,
    CreateVersion,
    CutDraft,
    CutRequest,
    DefaultProvider,
    DuplicateProject,
    ExpandRequest,
    GenerateAll,
    NormalizeBrief,
    PreviewVoice,
    ProviderValues,
    ReorderShots,
    RewriteShot,
    ScriptRequest,
    SplitShot,
    StoryboardRequest,
    SuggestTitles,
    TranscribeRequest,
    UpdateProject,
    UpdateShot,
)
from shotwright.config import PROVIDER_KINDS, ROOT_DIR, Settings, load_settings
from shotwright.core import languages, platforms
from shotwright.core.models import Project, Shot, ShotVisual, VoiceSettings, validate_id
from shotwright.core.store import ProjectNotFound, ProjectStore, read_json
from shotwright.logs import setup_logging
from shotwright.media import ffmpeg
from shotwright.media import fonts as font_lib
from shotwright.pipeline import voice as voice_stage
from shotwright.pipeline.render_types import RenderError
from shotwright.pipeline.shots import MAX_SEQUENCE
from shotwright.providers import registry
from shotwright.providers.base import BudgetExceeded, ChargeConfirmationRequired, ProviderError
from shotwright.providers.tts.edge import VOICES
from shotwright.services import batch as batch_service
from shotwright.services import brief as brief_service
from shotwright.services import (
    checkup,
    editing,
    footage,
    localize,
    music,
    prompting,
    pronounce,
    readiness,
    rewrite,
    storyboard,
    transcribe,
    visuals,
)
from shotwright.services import cost as cost_service
from shotwright.services import inspect as inspect_service
from shotwright.services import titles as titles_service
from shotwright.services.duplicate import duplicate_project

log = logging.getLogger("shotwright.api")

MAX_UPLOAD = 500 * 1024 * 1024
WEB_DIST = ROOT_DIR / "web" / "dist"
BGM_KEY = "__bgm__"
"""in_progress 里用这个伪镜头 id 表示「背景音乐检索中」。"""


def _host_allowed(host_header: str, settings: Settings) -> bool:
    """校验 Host 头，阻止 DNS rebinding。

    只接受本机地址；``allowed_hosts`` 配置里可以额外放行（例如局域网部署时填的 IP）。
    没有 Host 头的请求（命令行工具、测试客户端）直接放行。
    """
    if not host_header:
        return True
    value = host_header.strip()
    if value.startswith("["):
        # [::1]:8000 或 [::1]
        hostname = value[1 : value.index("]")] if "]" in value else value.strip("[]")
    elif value.count(":") > 1:
        # 裸 IPv6（RFC 上 Host 头应当加方括号，这里宽容处理）
        hostname = value
    else:
        hostname = value.rsplit(":", 1)[0]
    allowed = {"localhost", "127.0.0.1", "::1"}
    allowed.update(item.lower() for item in settings.allowed_hosts)
    return hostname.lower() in allowed


def _origin_allowed(request: Request, settings: Settings) -> bool:
    """校验 Origin 头，挡住来自其他网页的跨站写请求。

    只读请求（GET / HEAD / OPTIONS）不检查：同源页面加载资源时不一定带 Origin。
    没有 Origin 头时放行（命令行、原生客户端、同源导航都不会带）。
    """
    origin = request.headers.get("origin", "")
    if not origin:
        return True
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    from urllib.parse import urlsplit

    host = (urlsplit(origin).hostname or "").lower()
    if not host:
        # "null"（沙箱 iframe、file://）这类来源一律拒绝写操作。
        return False
    allowed = {"localhost", "127.0.0.1", "::1"}
    allowed.update(item.lower() for item in settings.allowed_hosts)
    return host in allowed


def create_app(settings: Settings | None = None, *, job_manager: JobManager | None = None) -> FastAPI:
    settings = settings or load_settings()
    log_path = setup_logging(settings)
    log.info("Shotwright %s 启动，日志写入 %s", __version__, log_path)
    ffmpeg.configure(settings.ffmpeg, settings.ffprobe)
    store = ProjectStore(settings.storage_dir)
    visuals.organize_all(store)
    jobs = job_manager or JobManager(store, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 启动后把上次没取回结果的收费任务捞回来，避免用户以为白花了钱。
        threading.Thread(target=_resume_all, name="shotwright-resume", daemon=True).start()
        yield
        jobs.shutdown()

    def _resume_all() -> None:
        """扫描所有项目里带 pending 的镜头，逐个继续轮询。

        恢复成功的素材要一并写回项目，否则登记到内存里的结果会随进程丢失。
        失败时只清掉 pending，让用户可以重新提交。
        """
        for project in store.list():
            pending_shots = [s for s in project.shots if s.visual.pending is not None]
            if not pending_shots:
                continue
            for shot in pending_shots:
                task_id = shot.visual.pending.task_id if shot.visual.pending else ""
                recovered: list[str] = []
                try:
                    recovered = visuals.resume_pending(store, settings, project, shot)
                except ProviderError as exc:
                    log.warning(
                        "项目 %s 镜头 %s 的收费任务 %s 恢复失败：%s",
                        project.id,
                        shot.id,
                        task_id,
                        exc,
                    )
                except Exception as exc:  # noqa: BLE001 - 后台线程不能因单个镜头崩掉
                    log.exception("恢复收费任务时出错：%s", exc)
                try:
                    with store.lock(project.id):
                        latest = store.load(project.id)
                        try:
                            target = latest.shot(shot.id)
                        except KeyError:
                            continue
                        for asset_id in recovered:
                            if asset_id in project.assets:
                                latest.assets.setdefault(asset_id, project.assets[asset_id])
                        if recovered:
                            merged = [*target.visual.candidates]
                            for asset_id in recovered:
                                if asset_id not in merged:
                                    merged.append(asset_id)
                            target.visual.candidates = merged[-visuals.MAX_CANDIDATES :]
                            target.visual.selected = recovered[0]
                            known = {item.asset_id for item in target.visual.takes}
                            target.visual.takes = [
                                item for item in target.visual.takes if item.asset_id in target.visual.candidates
                            ] + [
                                item
                                for item in shot.visual.takes
                                if item.asset_id in recovered and item.asset_id not in known
                            ]
                        target.visual.pending = None
                        store.save(latest)
                except OSError as exc:
                    log.warning("保存恢复结果失败：%s", exc)

    app = FastAPI(title="Shotwright", version=__version__, lifespan=lifespan)
    app.state.settings, app.state.store, app.state.jobs = settings, store, jobs
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 本地服务只监听本机，但要防 DNS rebinding：浏览器访问恶意域名时，域名会
    # 解析到 127.0.0.1，随后以该域名作为 Host 发请求。校验 Host / Origin 阻止这种访问。
    @app.middleware("http")
    async def _check_host(request: Request, call_next):
        if not _host_allowed(request.headers.get("host", ""), settings):
            log.warning("拒绝来自 Host=%s 的请求", request.headers.get("host", ""))
            return JSONResponse(
                status_code=403, content={"detail": errors.translate("不允许的访问来源", request)}
            )
        # Origin 只在跨域写请求里出现；校验它可以挡住来自恶意网页的 CSRF。
        if not _origin_allowed(request, settings):
            log.warning(
                "拒绝来自 Origin=%s 的 %s 请求", request.headers.get("origin", ""), request.method
            )
            return JSONResponse(
                status_code=403, content={"detail": errors.translate("不允许的访问来源", request)}
            )
        return await call_next(request)

    @app.exception_handler(ProjectNotFound)
    async def _not_found(request: Request, exc: ProjectNotFound):
        return JSONResponse(status_code=404, content={"detail": errors.translate("项目不存在", request)})

    @app.exception_handler(ChargeConfirmationRequired)
    async def _charge_required(request: Request, exc: ChargeConfirmationRequired):
        return JSONResponse(
            status_code=402,
            content={"detail": errors.translate(str(exc), request), "code": "confirm_charge"},
        )

    @app.exception_handler(ProviderError)
    async def _provider_error(request: Request, exc: ProviderError):
        log.warning("%s %s 失败：%s", request.method, request.url.path, exc)
        return JSONResponse(status_code=502, content={"detail": errors.translate(str(exc), request)})

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        """HTTPException 的 detail 默认原样返回；这里补上英文对照。"""
        detail = exc.detail
        if isinstance(detail, str):
            detail = errors.translate(detail, request)
        return JSONResponse(status_code=exc.status_code, content={"detail": detail})

    api = APIRouter(prefix="/api/v2")

    def _id(value: str) -> str:
        try:
            return validate_id(value)
        except ValueError:
            raise HTTPException(400, "无效的 id") from None

    def _mutate(project_id: str, change: Callable[[Project], object]) -> Project:
        """在项目锁内读取、修改、保存，避免并发编辑互相覆盖。"""
        with store.lock(_id(project_id)):
            project = store.load(project_id)
            try:
                change(project)
                Project.model_validate(project.model_dump())
            except KeyError:
                raise HTTPException(404, "镜头或素材不存在") from None
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            return store.save(project)

    def _clear_pending(project_id: str, shot_id: str) -> None:
        """清掉镜头里落盘的任务 id。

        生成成功与失败都会调用：成功后它已经没用，失败后留着会让界面一直显示"生成中"，
        而且下次启动还会拿它去轮询一个已经结束的任务。
        """
        try:
            with store.lock(project_id):
                latest = store.load(project_id)
                try:
                    target = latest.shot(shot_id)
                except KeyError:
                    return
                if target.visual.pending is None:
                    return
                target.visual.pending = None
                store.save(latest)
        except (ProjectNotFound, OSError) as exc:
            log.warning("清除镜头 %s 的任务标记失败：%s", shot_id, exc)

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

    # ---- 设置：在界面里改 config.toml --------------------------------------------

    def _mask(value: str) -> str:
        return "••••" + value[-4:] if len(value) > 8 else "••••"

    def _config_path() -> Path:
        if settings.config_path is None:
            raise HTTPException(409, "当前没有配置文件路径，不能在界面里修改配置")
        return settings.config_path

    def _spec_or_404(kind: str, provider_id: str):
        try:
            return registry.spec(kind, provider_id)
        except ProviderError:
            raise HTTPException(404, "服务不存在") from None

    @api.get("/settings")
    def read_settings():
        """设置页：当前的默认服务、代理和各服务填写的值（密钥只返回是否已填和后 4 位）。"""
        result: dict[str, dict] = {}
        for spec in registry.specs():
            saved = settings.provider_settings(spec.kind, spec.id)
            fields = {}
            for f in spec.fields:
                value = str(saved.get(f.key, ""))
                fields[f.key] = {"set": bool(value), "value": _mask(value) if f.secret and value else value}
            result.setdefault(spec.kind, {})[spec.id] = {
                "configured": registry.is_configured(spec, settings),
                "fields": fields,
            }
        return {
            "config_path": str(settings.config_path) if settings.config_path else "",
            "editable": settings.config_path is not None,
            "proxy": settings.proxy or "",
            "defaults": {kind: settings.default_provider(kind) for kind in PROVIDER_KINDS},
            "providers": result,
        }

    @api.put("/settings/providers/{kind}/{provider_id}")
    def save_provider_settings(kind: str, provider_id: str, body: ProviderValues):
        """保存一个服务的配置到 config.toml，立即生效（不用重启）。"""
        spec = _spec_or_404(kind, provider_id)
        known = {f.key for f in spec.fields}
        unknown = set(body.values) - known
        if unknown:
            raise HTTPException(422, f"不认识的配置项：{', '.join(sorted(unknown))}")
        changes: dict[str, str | None] = {key: (value.strip() or None) for key, value in body.values.items()}
        try:
            config_edit.update(_config_path(), ["providers", kind, provider_id], changes)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        current = settings.providers.setdefault(kind, {}).setdefault(provider_id, {})
        for key, value in changes.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        return read_settings()

    @api.put("/settings/defaults")
    def save_default_provider(body: DefaultProvider):
        _spec_or_404(body.kind, body.provider)
        try:
            config_edit.update(_config_path(), ["defaults"], {body.kind: body.provider})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        settings.defaults[body.kind] = body.provider
        return read_settings()

    @api.put("/settings/app")
    def save_app_settings(body: AppSettings):
        proxy = body.proxy.strip()
        if proxy and not proxy.startswith(("http://", "https://", "socks5://", "socks5h://")):
            raise HTTPException(422, "代理地址要以 http://、https:// 或 socks5:// 开头")
        try:
            config_edit.update(_config_path(), ["app"], {"proxy": proxy or None})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        settings.proxy = proxy or None
        return read_settings()

    @api.post("/settings/providers/{kind}/{provider_id}/test")
    def test_provider(kind: str, provider_id: str):
        """测试连接：发一个不花钱（或几乎不花钱）的请求，确认 Key、地址、模型名对不对。"""
        _spec_or_404(kind, provider_id)
        try:
            provider = registry.create(kind, provider_id, settings)
        except ProviderError as exc:
            return {"ok": False, "message": str(exc)}
        check = getattr(provider, "check", None)
        if check is None:
            return {"ok": False, "message": "这个服务暂不支持测试连接"}
        ok, message = check()
        return {"ok": ok, "message": message}

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

    # ---- 项目 ---------------------------------------------------------------

    @api.get("/projects")
    def list_projects():
        result = []
        for p in store.list():
            latest = _latest_render(p.id)
            source = _cover_source(p)
            result.append(
                {
                    "id": p.id,
                    "title": p.title or p.subject or "未命名项目",
                    "shots": len(p.shots),
                    "aspect": p.canvas.aspect,
                    "language": languages.normalize(p.language),
                    "group": p.group or p.id,
                    "origin": _origin(p),
                    "updated_at": p.updated_at,
                    "created_at": p.created_at,
                    # 最近一次成片：时长、生成时间，以及之后有没有再改过项目。
                    "render": (
                        {
                            "id": latest["render_id"],
                            "duration": latest.get("duration"),
                            "created_at": latest["created_at"],
                            "stale": latest.get("revision") != p.revision,
                        }
                        if latest
                        else None
                    ),
                    # 剪辑任务还没生成镜头时，从任务列表回到剪辑页要用到原片的素材 id。
                    "source_asset": next(
                        (a.id for a in p.assets.values() if a.kind in ("video", "audio")), None
                    )
                    if not p.shots
                    else None,
                    # 封面：最近的成片或第一个有画面的镜头；地址带上来源，换了来源浏览器会重新取。
                    "cover": f"/api/v2/projects/{p.id}/cover?k={source[1]}" if source else None,
                }
            )
        return result

    def _origin(p: Project) -> str:
        """任务来自创作还是剪辑。旧版剪辑页建的项目没有记 origin：有原声镜头、或识别过原片的算剪辑。"""
        if p.origin == "cut" or any(s.speech == "original" for s in p.shots):
            return "cut"
        transcripts = store.project_dir(p.id) / "cache" / "transcripts"
        if not p.subject and not p.idea and transcripts.is_dir() and any(transcripts.iterdir()):
            return "cut"
        return "create"

    def _latest_render(project_id: str) -> dict | None:
        directory = store.project_dir(project_id) / "renders"
        best: dict | None = None
        if directory.is_dir():
            for child in directory.iterdir():
                video = child / "video.mp4"
                if not video.is_file():
                    continue
                created = video.stat().st_mtime
                if best is not None and created <= best["created_at"]:
                    continue
                data = read_json(child / "render.json")
                if data and data.get("render_id"):
                    best = {**data, "created_at": created}
        return best

    def _cover_source(project: Project) -> tuple[Path, str] | None:
        """封面取自哪个文件：最近的成片，没有成片时用第一个有画面的镜头。返回（文件, 缓存键）。"""
        latest = _latest_render(project.id)
        if latest:
            video = store.project_dir(project.id) / "renders" / latest["render_id"] / "video.mp4"
            return video, f"r{latest['render_id']}"
        for shot in project.shots:
            visual = shot.visual
            asset = project.assets.get(visual.selected or "") if visual.mode != "color" else None
            if asset is not None and asset.kind in ("image", "video"):
                path = store.asset_path(project, asset.id)
                if path.is_file():
                    return path, f"a{asset.id}"
        return None

    @api.get("/projects/{project_id}/cover")
    def project_cover(project_id: str):
        """项目封面（480 像素宽的 JPEG），按来源缓存。"""
        project = store.load(_id(project_id))
        source = _cover_source(project)
        if source is None:
            raise HTTPException(404, "项目还没有画面")
        path, key = source
        target = store.cache_dir(project.id, "cover") / f"{key}.jpg"
        if not target.is_file():
            scale = "scale='min(480,iw)':-2"
            temp = target.with_suffix(".tmp.jpg")
            attempts = [["-ss", "1", "-i", str(path)], ["-i", str(path)]] if path.suffix != ".png" else [["-i", str(path)]]
            for inputs in attempts:
                try:
                    ffmpeg.run_ffmpeg([*inputs, "-frames:v", "1", "-vf", scale, "-q:v", "4", str(temp)])
                except Exception:  # noqa: BLE001 —— 短视频从第 1 秒取不到帧时退回第一帧
                    continue
                if temp.is_file() and temp.stat().st_size > 0:
                    temp.replace(target)
                    break
            if not target.is_file():
                raise HTTPException(404, "封面生成失败")
        return FileResponse(target, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @api.post("/projects", status_code=201)
    def create_project(body: CreateProject):
        lang = languages.get(body.language)
        project = Project(
            title=body.title,
            subject=body.subject,
            idea=body.idea,
            target_seconds=body.duration,
            video_type=body.video_type,
            origin=body.origin,
        )
        # 新项目要先确认素材（全片设定）才能生成 AI 画面。
        project.look.confirmed = False
        if body.canvas:
            project.canvas = body.canvas
        if not body.voice and settings.default_provider("tts"):
            # 没指定配音时用配置里 [defaults] tts 的服务，而不是固定用 Edge。
            project.voice = VoiceSettings(provider=settings.default_provider("tts"))
        localize.apply_language(project, lang.code)
        if body.voice:
            # 用户显式指定的配音优先于语言默认值。
            project.voice = body.voice
        if body.mode == "llm":
            if not body.subject.strip():
                raise HTTPException(422, "请填写视频诉求")
            llm = registry.create("llm", None, settings)
            board = storyboard.write_storyboard(
                llm,
                body.subject.strip(),
                language=lang.prompt_name,
                prompt_lang=prompting.prompt_language(project),
                shots=body.shots,
                duration=body.duration,
                video_type=body.video_type,
                aspect=project.canvas.aspect,
            )
            project.title = project.title or board.title
            project.shots = board.shots
            project.look = board.look
            project.look.confirmed = False
            if body.video_type:
                storyboard.suggest_sources(project.shots, visuals.available_sources(settings), project.look)
        elif body.mode == "split":
            if not body.script.strip():
                raise HTTPException(422, "请填写视频文案")
            project.shots = storyboard.split_script(body.script)
        # 创作记录：生成分镜时的文案，之后改镜头不影响。
        project.script = [shot.text for shot in project.shots if shot.text.strip()][:60]
        project.title = project.title or body.subject[:40] or "未命名项目"
        return store.create(project)

    def _materials(items) -> list[brief_service.Material]:
        return [
            brief_service.Material(kind=item.kind, role=item.role, name=item.name, note=item.note, asset_id=item.asset_id)
            for item in items
        ]

    @api.post("/brief/normalize")
    def normalize_brief(body: NormalizeBrief):
        """把用户随手写的想法按视频类型的模板整理成完整诉求。不建项目、不收费。"""
        lang = languages.get(body.language)
        llm = registry.create("llm", None, settings)
        text = brief_service.normalize_brief(
            llm,  # type: ignore[arg-type]
            body.idea,
            template=body.template,
            video_type=body.video_type,
            language=lang.prompt_name,
            aspect=body.aspect,
            duration=body.duration,
            materials=_materials(body.materials),
        )
        return {"brief": text}

    @api.post("/brief/script")
    def write_script(body: ScriptRequest):
        """按诉求写口播脚本（每段一个镜头），交给用户修改。不建项目、不收费。"""
        lang = languages.get(body.language)
        llm = registry.create("llm", None, settings)
        title, lines = brief_service.write_script(
            llm,  # type: ignore[arg-type]
            body.brief,
            video_type=body.video_type,
            language=lang.prompt_name,
            aspect=body.aspect,
            duration=body.duration,
            materials=_materials(body.materials),
        )
        return {"title": title, "lines": lines, "seconds": brief_service.script_seconds(lines)}

    @api.post("/projects/{project_id}/storyboard")
    def project_storyboard(project_id: str, body: StoryboardRequest):
        """给项目写分镜：有定稿脚本时按脚本一段一个镜头，只配画面；再把上传的素材放到该放的地方。"""
        project = store.load(_id(project_id))
        materials = _materials(body.materials)
        for item in materials:
            if item.asset_id is not None and _id(item.asset_id) not in project.assets:
                raise HTTPException(404, "素材不存在")
        lang = languages.get(project.language)
        llm = registry.create("llm", None, settings)
        board = storyboard.write_storyboard(
            llm,  # type: ignore[arg-type]
            body.subject.strip(),
            language=lang.prompt_name,
            prompt_lang=prompting.prompt_language(project),
            duration=body.duration,
            video_type=project.video_type,
            aspect=project.canvas.aspect,
            script=body.script,
            materials=brief_service.describe_materials(materials),
        )

        def change(latest: Project) -> None:
            latest.subject = body.subject.strip()
            if body.idea.strip():
                latest.idea = body.idea.strip()
            latest.script = [shot.text for shot in board.shots if shot.text.strip()][:60]
            if body.duration:
                latest.target_seconds = body.duration
            if not latest.title or latest.title == "未命名项目":
                latest.title = board.title or body.subject[:40]
            latest.shots = board.shots
            latest.look = board.look
            latest.look.confirmed = False  # 新写的主体要先准备、确认，才能生成 AI 画面
            if latest.video_type:
                storyboard.suggest_sources(latest.shots, visuals.available_sources(settings), latest.look)
            brief_service.apply_materials(latest, materials, board.footage)

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/materials")
    def apply_materials(project_id: str, body: ApplyMaterials):
        """把新建时上传的素材放到该放的地方（不调用大模型）。"""

        def change(latest: Project) -> None:
            materials = _materials(body.materials)
            for item in materials:
                if item.asset_id is None or item.asset_id not in latest.assets:
                    raise KeyError(item.asset_id)
            brief_service.apply_materials(latest, materials)

        return _mutate(project_id, change)

    @api.get("/projects/{project_id}")
    def get_project(project_id: str):
        return store.load(_id(project_id))

    @api.patch("/projects/{project_id}")
    def update_project(project_id: str, body: UpdateProject):
        def change(project: Project) -> None:
            if body.language is not None:
                if body.language not in languages.LANGUAGES:
                    raise ValueError("不支持的语言")
                localize.apply_language(project, body.language)
            for name in (
                "title",
                "subject",
                "idea",
                "script",
                "video_type",
                "titles",
                "chain_frames",
                "target_seconds",
                "canvas",
                "voice",
                "pronunciations",
                "subtitle",
                "overlays",
                "transition",
                "bgm",
                "sfx",
                "delivery",
                "references",
                "ai_badge",
            ):
                value = getattr(body, name)
                if value is not None:
                    setattr(project, name, value)
            if body.look is not None:
                project.look = readiness.carry_over(project.look, body.look)
            if "loudness" in body.model_fields_set:
                project.loudness = body.loudness
            if "budget" in body.model_fields_set:
                project.budget = body.budget
            if project.sfx.ambience and (
                project.sfx.ambience not in project.assets or project.assets[project.sfx.ambience].kind != "audio"
            ):
                raise ValueError("环境声必须是项目里的音频素材")
            if project.bgm.asset_id and project.bgm.asset_id not in project.assets:
                raise ValueError("背景音乐素材不存在")
            for item in project.look.subjects:
                for image in item.images:
                    asset = project.assets.get(image)
                    if asset is None:
                        raise ValueError("全片设定引用的参考素材不存在")
                    if asset.kind != "image":
                        raise ValueError("参考素材必须是图片")
            for ref in project.references:
                asset = project.assets.get(ref.asset_id)
                if asset is None:
                    raise ValueError("参考素材不存在")
                if asset.kind != "image":
                    raise ValueError("参考素材必须是图片")

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/look/{code}/image")
    def subject_image(project_id: str, code: str, body: ChargeConsent | None = None):
        """给全片设定里的主体生成定妆图（AI 图片，收费，需要 confirm_charge）。"""
        project = store.load(_id(project_id))
        if not any(s.code == code for s in project.look.subjects):
            raise HTTPException(404, "主体不存在")
        asset_id = visuals.generate_subject_image(
            store, settings, project, code, confirm_charge=(body or ChargeConsent()).confirm_charge
        )

        def change(latest: Project) -> None:
            latest.assets.setdefault(asset_id, project.assets[asset_id])
            latest.cost_credits = max(latest.cost_credits, project.cost_credits)
            subject = next((s for s in latest.look.subjects if s.code == code), None)
            if subject is None:
                raise KeyError(code)
            if asset_id not in subject.images:
                subject.images.append(asset_id)

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/look/confirm")
    def confirm_look(project_id: str):
        """确认素材（全片设定）已准备好，之后才能生成 AI 画面。没准备好时返回 409 并说明缺什么。"""

        def change(project: Project) -> None:
            try:
                readiness.confirm(project)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/look/{code}/describe")
    def describe_subject(project_id: str, code: str):
        """看主体的参考图写外观描述（领型、有无帽子、拉链、缝线、Logo 位置），只返回文字，不保存。不收费。"""
        project = store.load(_id(project_id))
        try:
            text = inspect_service.describe_subject(settings, store, project, code)
        except KeyError:
            raise HTTPException(404, "主体不存在") from None
        return {"appearance": text}

    @api.post("/projects/{project_id}/pronunciations/suggest")
    def suggest_pronunciations(project_id: str):
        """AI 检查文案里容易读错的词（多音字等），追加到读音纠正表。不收费。"""
        project = store.load(_id(project_id))
        llm = registry.create("llm", None, settings)
        found = pronounce.suggest(llm, project)  # type: ignore[arg-type]

        def change(latest: Project) -> None:
            known = {item.word for item in latest.pronunciations}
            latest.pronunciations += [item for item in found if item.word not in known]

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/voice/preview")
    def preview_voice(project_id: str, body: PreviewVoice):
        """按当前配音设置和读音纠正合成一句试听。"""
        project = store.load(_id(project_id))
        try:
            path = voice_stage.preview_voice(store, settings, project, body.text)
        except RenderError as exc:
            raise HTTPException(422, str(exc)) from None
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from None
        media_type = "audio/mpeg" if path.suffix == ".mp3" else "audio/mp4" if path.suffix == ".m4a" else "audio/wav"
        return FileResponse(path, media_type=media_type)

    @api.post("/projects/{project_id}/subtitle/keywords")
    def suggest_keywords(project_id: str):
        """AI 挑选字幕高亮关键词，替换 subtitle.keywords。不生成画面、不收费。"""
        project = store.load(_id(project_id))
        llm = registry.create("llm", None, settings)
        words = titles_service.suggest_keywords(llm, project)  # type: ignore[arg-type]

        def change(latest: Project) -> None:
            latest.subtitle.keywords = words

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/titles/suggest")
    def suggest_titles(project_id: str, body: SuggestTitles | None = None):
        """AI 生成标题：候选大标题存进 titles.candidates，可选地为镜头写小标题。不生成画面、不收费。"""
        body = body or SuggestTitles()
        project = store.load(_id(project_id))
        llm = registry.create("llm", None, settings)
        headlines, subtitle, shot_titles = titles_service.suggest_titles(llm, project)  # type: ignore[arg-type]

        def change(latest: Project) -> None:
            latest.titles.candidates = headlines
            if subtitle and not latest.titles.intro.subtitle:
                latest.titles.intro.subtitle = subtitle
            if body.apply_headline:
                latest.titles.headline.text = headlines[0]
                latest.titles.headline.enabled = True
            if body.shot_titles:
                ids = [shot.id for shot in project.shots]
                for index, title in shot_titles.items():
                    try:
                        target = latest.shot(ids[index - 1])
                    except (KeyError, IndexError):
                        continue
                    if not target.title.strip():
                        target.title = title

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/duplicate", status_code=201)
    def duplicate(project_id: str, body: DuplicateProject | None = None):
        """把已有项目复制成新项目（素材、设置、镜头和渲染缓存一起复制），返回新项目。"""
        with store.lock(_id(project_id)):
            project = store.load(project_id)
            return duplicate_project(store, project, title=body.title if body else None)

    @api.get("/projects/{project_id}/versions")
    def list_versions(project_id: str):
        """同一个视频的所有语言版本（包括自己）。"""
        project = store.load(_id(project_id))
        return [
            {
                "id": p.id,
                "title": p.title,
                "language": languages.normalize(p.language),
                "updated_at": p.updated_at,
            }
            for p in localize.versions(store, project)
        ]

    @api.post("/projects/{project_id}/versions", status_code=201)
    def create_version(project_id: str, body: CreateVersion):
        """调用大模型把文案翻译成另一种语言，生成一个新项目（画面素材共用）。"""
        project = store.load(_id(project_id))
        if body.language not in languages.LANGUAGES:
            raise HTTPException(422, "不支持的语言")
        llm = registry.create("llm", None, settings)
        return localize.create_language_version(store, llm, project, body.language)  # type: ignore[arg-type]

    @api.delete("/projects/{project_id}", status_code=204)
    def delete_project(project_id: str, force: bool = False):
        pid = _id(project_id)
        active = [j for j in jobs.list(pid) if j.state in ("queued", "running")]
        if active and not force:
            # 渲染中途删目录会让任务写文件时抛难以理解的错误。
            raise HTTPException(409, "这个项目正在渲染，请等它结束，或加 ?force=true 强制删除")
        for job in active:
            jobs.cancel(job.id)
        store.delete(pid)

    # ---- 镜头 ---------------------------------------------------------------

    @api.post("/projects/{project_id}/shots", status_code=201)
    def add_shot(project_id: str, body: AddShot):
        def change(project: Project) -> None:
            shot = Shot(text=body.text, visual=ShotVisual())
            ids = [s.id for s in project.shots]
            index = ids.index(body.after) + 1 if body.after in ids else len(ids)
            project.shots.insert(index, shot)

        return _mutate(project_id, change)

    @api.put("/projects/{project_id}/shots/order")
    def reorder_shots(project_id: str, body: ReorderShots):
        def change(project: Project) -> None:
            current = {s.id: s for s in project.shots}
            if sorted(body.order) != sorted(current):
                raise ValueError("新顺序必须包含全部镜头且不能重复")
            project.shots = [current[shot_id] for shot_id in body.order]

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/shots/{shot_id}/split")
    def split_shot(project_id: str, shot_id: str, body: SplitShot):
        """在镜头片段的第 ``at`` 秒处拆成两个镜头（只适用于视频镜头）。"""

        def change(project: Project) -> None:
            editing.split_shot(project, _id(shot_id), body.at, store)

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/shots/{shot_id}/merge")
    def merge_shot(project_id: str, shot_id: str):
        """把镜头和后一个镜头合并；同一段视频里相邻的片段会连成一段。"""

        def change(project: Project) -> None:
            editing.merge_with_next(project, _id(shot_id))

        return _mutate(project_id, change)

    @api.patch("/projects/{project_id}/shots/{shot_id}")
    def update_shot(project_id: str, shot_id: str, body: UpdateShot):
        # 只改审片标记时不动镜头状态：审片不影响画面。
        review_only = body.model_fields_set == {"visual"} and body.visual is not None and (
            body.visual.model_fields_set <= {"review", "review_note"}
        )

        def change(project: Project) -> None:
            shot = project.shot(_id(shot_id))
            if body.text is not None:
                shot.text = body.text
            if body.role is not None:
                shot.role = body.role
            if body.speech is not None:
                shot.speech = body.speech
            if body.title is not None:
                shot.title = body.title.strip()
            if body.clear_duration:
                shot.duration = None
            elif body.duration is not None:
                shot.duration = body.duration
            if body.clear_voice:
                shot.voice = None
            elif body.voice is not None:
                shot.voice = body.voice
            if body.visual is not None:
                patch = body.visual.model_dump(exclude_unset=True)
                if patch.pop("clear_first_frame", False):
                    patch["first_frame"] = None
                for field in ("selected", "first_frame"):
                    if patch.get(field) and patch[field] not in project.assets:
                        raise ValueError("所选素材不存在")
                if "sequence" in patch:
                    sequence = list(dict.fromkeys(patch["sequence"] or []))
                    if any(item not in project.assets for item in sequence):
                        raise ValueError("所选素材不存在")
                    if any(project.assets[item].kind not in ("video", "image") for item in sequence):
                        raise ValueError("只能多选图片或视频素材")
                    current = patch.get("candidates", shot.visual.candidates)
                    missing = [item for item in sequence if item not in current]
                    if missing:
                        patch["candidates"] = [*current, *missing][-visuals.MAX_CANDIDATES :]
                    patch["sequence"] = sequence
                if patch.get("first_frame") and project.assets[patch["first_frame"]].kind != "image":
                    raise ValueError("首帧必须是图片")
                if "refs" in patch:
                    refs = list(dict.fromkeys(patch["refs"] or []))
                    if any(item not in project.assets for item in refs):
                        raise ValueError("所选素材不存在")
                    if any(project.assets[item].kind != "image" for item in refs):
                        raise ValueError("参考素材必须是图片")
                    patch["refs"] = refs
                if "terms" in patch:
                    patch["terms"] = [t.strip()[:80] for t in patch["terms"] if t.strip()]
                current = patch.get("candidates", shot.visual.candidates)
                if patch.get("selected") and patch["selected"] not in current:
                    patch["candidates"] = [*current, patch["selected"]][-visuals.MAX_CANDIDATES :]
                if patch.pop("clear_trim_end", False):
                    patch["trim_end"] = None
                mode = patch.pop("mode", None)
                if mode is not None:
                    # 切换来源时自动选中该来源的素材（显式传了 selected 时以它为准）。
                    visuals.switch_mode(project, shot, mode)
                edit_fields = {"trim_start", "trim_end", "focus_x", "focus_y"} & patch.keys()
                if edit_fields:
                    # 入出点和取景记在当前选中的素材名下；之前针对别的素材的设置先清掉。
                    target = patch.get("selected", shot.visual.selected)
                    if target is None:
                        raise ValueError("请先选中素材再设置入出点或取景")
                    if shot.visual.edit_asset != target:
                        patch = {"trim_start": 0.0, "trim_end": None, "focus_x": 0.5, "focus_y": 0.5, **patch}
                    patch["edit_asset"] = target
                merged = {**shot.visual.model_dump(), **patch}
                # 选中的素材不重复出现在多选列表里；多选最多 MAX_SEQUENCE 段（含选中的那段）。
                merged["sequence"] = [
                    item for item in merged.get("sequence") or [] if item != merged.get("selected")
                ][: MAX_SEQUENCE - 1]
                if not merged.get("selected"):
                    merged["sequence"] = []
                try:
                    shot.visual = ShotVisual.model_validate(merged)
                except ValidationError as exc:
                    # 只把第一条原因（例如「出点必须晚于入点」）返回给界面，便于翻译。
                    message = str(exc.errors()[0].get("msg", exc))
                    raise ValueError(message.removeprefix("Value error, ")) from None
            if not review_only:
                shot.status, shot.error = "draft", None

        return _mutate(project_id, change)

    @api.delete("/projects/{project_id}/shots/{shot_id}")
    def delete_shot(project_id: str, shot_id: str):
        def change(project: Project) -> None:
            project.shots.remove(project.shot(_id(shot_id)))

        return _mutate(project_id, change)

    @api.delete("/projects/{project_id}/shots/{shot_id}/candidates/{asset_id}")
    def remove_candidate(project_id: str, shot_id: str, asset_id: str):
        """从镜头的候选素材里删掉一个；删的是选中的素材时自动换成下一个。"""

        def change(project: Project) -> None:
            shot = project.shot(_id(shot_id))
            if shot.visual.locked:
                raise HTTPException(409, "镜头已锁定，请先解锁")
            try:
                visuals.remove_candidate(project, shot, _id(asset_id))
            except KeyError:
                raise HTTPException(404, "候选素材不存在") from None
            shot.status, shot.error = "draft", None

        return _mutate(project_id, change)

    # ---- 剪辑：识别原声，按文字剪 ---------------------------------------------------

    def _media_asset(project: Project, asset_id: str):
        asset = project.assets.get(_id(asset_id))
        if asset is None:
            raise HTTPException(404, "素材不存在")
        return asset

    @api.get("/projects/{project_id}/transcripts/{asset_id}")
    def read_transcript(project_id: str, asset_id: str):
        """识别状态和结果；完成时附带语气词的下标（剪辑页一键勾选）。"""
        project = store.load(_id(project_id))
        _media_asset(project, asset_id)
        data = transcribe.load(store, project.id, asset_id)
        if data.get("state") == "done":
            words = transcribe.words_of(data)
            data = {**data, "fillers": transcribe.filler_indexes(words, data.get("language") or project.language)}
        return data

    @api.post("/projects/{project_id}/transcripts/{asset_id}")
    def start_transcript(project_id: str, asset_id: str, body: TranscribeRequest | None = None):
        """开始识别（后台进行），之后轮询 GET 查看进度。"""
        project = store.load(_id(project_id))
        _media_asset(project, asset_id)
        body = body or TranscribeRequest()
        return transcribe.start(store, settings, project, asset_id, provider_id=body.provider, language=body.language)

    @api.post("/projects/{project_id}/transcripts/{asset_id}/retakes")
    def suggest_retakes(project_id: str, asset_id: str):
        """AI 去口误：找出重说、说一半放弃、和内容无关的句子，返回其中所有词的下标（不收费的大模型调用）。"""
        project = store.load(_id(project_id))
        _media_asset(project, asset_id)
        data = transcribe.load(store, project.id, asset_id)
        if data.get("state") != "done":
            raise HTTPException(409, "这段原片还没有识别完成")
        llm = registry.create("llm", None, settings)
        return {"remove": transcribe.suggest_retakes(llm, data)}  # type: ignore[arg-type]

    @api.put("/projects/{project_id}/transcripts/{asset_id}/draft")
    def save_cut_draft(project_id: str, asset_id: str, body: CutDraft):
        """保存剪辑进度：页面上每次删词、改字后自动保存。"""
        project = store.load(_id(project_id))
        _media_asset(project, asset_id)
        try:
            return transcribe.save_draft(store, project.id, asset_id, body.model_dump())
        except ProviderError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api.post("/projects/{project_id}/cut")
    def cut_by_text(project_id: str, body: CutRequest):
        """按剪辑结果生成原声镜头，替换项目里现有的镜头。"""
        texts: dict[int, str] = {}
        for key, value in body.texts.items():
            try:
                texts[int(key)] = value[:200]
            except ValueError:
                raise HTTPException(422, "改字的下标不对") from None

        def change(project: Project) -> None:
            _media_asset(project, body.asset_id)
            transcribe.build_shots(
                store,
                project,
                body.asset_id,
                removed=set(body.removed),
                max_pause=body.max_pause,
                split_scenes=body.split_scenes,
                texts=texts,
            )
            # 生成分镜用的设置也记为剪辑进度，回到剪辑页时还是这一版。
            transcribe.save_draft(
                store,
                project.id,
                body.asset_id,
                {
                    "removed": body.removed,
                    "texts": body.texts,
                    "pauses": body.max_pause is not None,
                    "max_pause": body.max_pause,
                    "scenes": body.split_scenes,
                },
            )
            project.video_type = "footage"
            project.origin = "cut"

        return _mutate(project_id, change)

    # 正在检索或生成的镜头：页面刷新后仍能显示"生成中"，并拒绝重复提交。
    in_progress: dict[tuple[str, str], dict] = {}
    in_progress_lock = threading.Lock()

    @api.get("/projects/{project_id}/in-progress")
    def shots_in_progress(project_id: str):
        _id(project_id)
        now = time.time()
        result: dict[str, dict] = {}
        # 进程重启后正在恢复的收费任务：项目里带 pending 的镜头同样算"生成中"。
        try:
            project = store.load(project_id)
        except ProjectNotFound:
            project = None
        if project is not None:
            for shot in project.shots:
                pending = shot.visual.pending
                if pending is not None:
                    result[shot.id] = {
                        "mode": "resume",
                        "seconds": round(max(0.0, now - pending.since), 1),
                    }
        with in_progress_lock:
            for (pid, shot_id), info in in_progress.items():
                if pid == project_id:
                    result[shot_id] = {
                        "mode": info["mode"],
                        "seconds": round(now - info["since"], 1),
                    }
        return result

    @api.post("/projects/{project_id}/shots/{shot_id}/candidates")
    def refresh_candidates(project_id: str, shot_id: str, body: CandidatesRequest | None = None):
        """检索（stock）或生成（ai / image）新的候选素材。可能耗时较长。

        收费的生成必须带 ``confirm_charge: true``，否则返回 402。
        """
        _id(project_id)
        project = store.load(project_id)
        shot = project.shot(_id(shot_id)) if any(s.id == shot_id for s in project.shots) else None
        if shot is None:
            raise HTTPException(404, "镜头不存在")
        if shot.visual.locked:
            raise HTTPException(409, "镜头已锁定，请先解锁")
        body = body or CandidatesRequest()
        if (body.edit_selected or shot.visual.mode in ("ai", "image")) and readiness.generation_locked(project):
            raise HTTPException(409, readiness.LOCKED_MESSAGE)
        confirmed = body.confirm_charge
        edit = body.edit_selected
        keyframes = body.keyframes if shot.visual.mode == "ai" and not edit else 0
        key = (project_id, shot_id)
        with in_progress_lock:
            if key in in_progress:
                # 同一个镜头不允许重复提交，避免连点造成重复扣费。
                raise HTTPException(409, "这个镜头正在生成，请等它完成")
            in_progress[key] = {
                "mode": "image" if keyframes else "ai" if edit else shot.visual.mode,
                "since": time.time(),
            }
        # 关键帧只是候选图片，不改变当前选中的画面。
        generated = (edit or shot.visual.mode in ("ai", "image")) and not keyframes

        def change(latest: Project) -> None:
            for asset_id, asset in project.assets.items():
                latest.assets.setdefault(asset_id, asset)
            target = latest.shot(shot_id)
            target.visual.candidates = shot.visual.candidates
            target.visual.takes = shot.visual.takes
            # 生成已经结束，落盘的任务 id 必须一并清掉，否则 /in-progress 会一直报"生成中"。
            target.visual.pending = None
            if edit:
                # 改画面的结果是 AI 视频，切到 AI 视频来源，新画面才会出现在候选里。
                target.visual.mode = "ai"
            if generated or target.visual.selected is None or target.visual.selected not in latest.assets:
                # 刚生成的画面花了钱，直接选中它。
                target.visual.selected = shot.visual.selected

        try:
            visuals.refresh_candidates(
                store,
                settings,
                project,
                shot,
                confirm_charge=confirmed,
                edit_selected=edit,
                count=body.count,
                keyframes=keyframes,
                quality=body.quality,
            )
            return _mutate(project_id, change)
        finally:
            # 保存之后再移除，页面看到"完成"时就能读到新素材。
            with in_progress_lock:
                in_progress.pop(key, None)
            # 失败路径不会走到 change()，这里再清一次，保证不会留下"生成中"。
            _clear_pending(project_id, shot_id)

    _VISUAL_TEXT = ("prompt", "image_prompt", "camera", "link")

    @api.post("/projects/{project_id}/shots/{shot_id}/rewrite")
    def rewrite_shot(project_id: str, shot_id: str, body: RewriteShot):
        """单镜头 AI 改写：按一句要求只改这个镜头的画面，保持全片设定和衔接。不生成画面、不收费。"""
        project = store.load(_id(project_id))
        shot = project.shot(_id(shot_id))
        llm = registry.create("llm", None, settings)
        rewrite.rewrite_shot(llm, project, shot, body.instruction)  # type: ignore[arg-type]

        def change(latest: Project) -> None:
            target = latest.shot(shot_id)
            for name in _VISUAL_TEXT:
                setattr(target.visual, name, getattr(shot.visual, name))

        return _mutate(project_id, change)

    # ---- 一键生成 ------------------------------------------------------------

    @api.get("/projects/{project_id}/generate-all")
    def generate_all_status(project_id: str, quality: Literal["draft", "final"] = "final", only_redo: bool = False):
        """要生成哪些镜头、预计多少钱，以及最近一次一键生成的进度。"""
        project = store.load(_id(project_id))
        job = batch_service.get(project_id)
        return {
            "plan": batch_service.plan(project, settings, quality, only_redo),
            "job": job.to_dict() if job else None,
        }

    @api.post("/projects/{project_id}/generate-all")
    def generate_all(project_id: str, body: GenerateAll):
        """把还没有画面的 AI 镜头和标记重做的镜头排队，后台依次生成。

        用到收费服务时必须带 ``confirm_charge: true``，否则返回 402。
        """
        project = store.load(_id(project_id))
        if readiness.generation_locked(project):
            raise HTTPException(409, readiness.LOCKED_MESSAGE)
        plan = batch_service.plan(project, settings, body.quality, body.only_redo)
        if not plan["items"]:
            raise HTTPException(409, "没有需要生成的镜头")
        if (plan["paid"] or plan["total"] > 0) and not body.confirm_charge:
            raise ChargeConfirmationRequired("一键生成会调用收费服务，需要确认费用后才会开始")
        if project.budget is not None and project.cost_credits + plan["total"] > project.budget + 1e-9:
            raise HTTPException(409, "预计费用超出项目费用上限，可以在渲染面板提高上限，或者少选几个镜头")
        try:
            job = batch_service.start(
                project_id, [item["shot_id"] for item in plan["items"]], body.quality, plan["total"]
            )
        except batch_service.BatchRunning as exc:
            raise HTTPException(409, str(exc)) from None
        threading.Thread(target=_run_batch, args=(job,), name=f"batch-{project_id}", daemon=True).start()
        return job.to_dict()

    @api.post("/projects/{project_id}/generate-all/cancel")
    def cancel_generate_all(project_id: str):
        """停止一键生成：正在生成的镜头会做完，排在后面的不再提交。"""
        job = batch_service.cancel(_id(project_id))
        if job is None:
            raise HTTPException(404, "任务已结束或不存在")
        return job.to_dict()

    def _run_batch(job: batch_service.BatchJob) -> None:
        project_id = job.project_id
        try:
            for shot_id in job.shot_ids:
                if job.cancel_requested:
                    break
                job.current = shot_id
                try:
                    project = store.load(project_id)
                    shot = next((item for item in project.shots if item.id == shot_id), None)
                    if shot is None or batch_service.needs_generation(project, shot) is None:
                        # 排队期间镜头被删掉、锁定、或者已经手动生成好了。
                        job.skipped.append(shot_id)
                        continue
                    redo = shot.visual.review == "redo"
                    if redo and shot.visual.review_note:
                        try:
                            rewrite_shot(project_id, shot_id, RewriteShot(instruction=shot.visual.review_note[:500]))
                        except Exception as exc:  # noqa: BLE001 - 改写失败就按原来的描述生成
                            log.warning("一键生成：镜头 %s 按修改意见改写失败：%s", shot_id, exc)
                    refresh_candidates(
                        project_id, shot_id, CandidatesRequest(confirm_charge=True, quality=job.quality)
                    )
                    if redo:

                        def clear_review(latest: Project, shot_id: str = shot_id) -> None:
                            target = latest.shot(shot_id)
                            target.visual.review, target.visual.review_note = "", ""

                        _mutate(project_id, clear_review)
                    job.done.append(shot_id)
                except BudgetExceeded as exc:
                    job.failed.append({"shot_id": shot_id, "error": str(exc)})
                    job.stopped_reason = str(exc)
                    break
                except HTTPException as exc:
                    job.failed.append({"shot_id": shot_id, "error": str(exc.detail)})
                except Exception as exc:  # noqa: BLE001 - 一个镜头失败不影响后面的
                    log.warning("一键生成：镜头 %s 失败：%s", shot_id, exc)
                    job.failed.append({"shot_id": shot_id, "error": str(exc) or type(exc).__name__})
        finally:
            batch_service.finish(job)

    @api.post("/projects/{project_id}/expand")
    def expand_project(project_id: str, body: ExpandRequest | None = None):
        """一键补全：扩写过短的画面描述、补衔接说明，没有全片设定时一并生成。不生成画面、不收费。"""
        project = store.load(_id(project_id))
        llm = registry.create("llm", None, settings)
        changed = rewrite.expand_project(llm, project, only_short=(body or ExpandRequest()).only_short)  # type: ignore[arg-type]

        def change(latest: Project) -> None:
            if latest.look.is_empty() and not project.look.is_empty():
                latest.look = project.look
                latest.look.confirmed = False  # 补出来的主体同样要先确认
            for shot_id in changed:
                try:
                    target = latest.shot(shot_id)
                except KeyError:
                    continue
                source = project.shot(shot_id)
                for name in _VISUAL_TEXT:
                    setattr(target.visual, name, getattr(source.visual, name))

        return _mutate(project_id, change)

    # ---- 素材 ---------------------------------------------------------------

    @api.post("/projects/{project_id}/assets", status_code=201)
    def upload_asset(project_id: str, file: UploadFile = File(...), as_audio: bool = False):
        """上传素材。``as_audio=true`` 时按背景音乐处理（音乐文件常装在视频容器里）。"""
        _id(project_id)
        if not store.exists(project_id):
            raise ProjectNotFound(project_id)
        filename = Path(file.filename or "upload").name
        with tempfile.TemporaryDirectory(prefix="shotwright-upload-") as temp:
            path = Path(temp) / ("upload" + Path(filename).suffix.lower()[:8])
            size = 0
            with path.open("wb") as handle:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, "文件超过 500MB")
                    handle.write(chunk)
            with store.lock(project_id):
                project = store.load(project_id)
                asset = visuals.add_upload(
                    store, project, path, filename=filename, prefer_audio=as_audio
                )
                store.save(project)
        return asset

    @api.get("/projects/{project_id}/assets/{asset_id}")
    def asset_file(project_id: str, asset_id: str):
        project = store.load(_id(project_id))
        if _id(asset_id) not in project.assets:
            raise HTTPException(404, "素材不存在")
        path = store.asset_path(project, asset_id)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type)

    @api.post("/projects/{project_id}/footage", status_code=201)
    def import_footage(project_id: str, file: UploadFile = File(...), replace_shots: bool = False):
        """导入一段原片，按画面变化自动切成镜头。"""
        _id(project_id)
        if not store.exists(project_id):
            raise ProjectNotFound(project_id)
        filename = Path(file.filename or "footage").name
        with tempfile.TemporaryDirectory(prefix="shotwright-footage-") as temp:
            path = Path(temp) / ("footage" + Path(filename).suffix.lower()[:8])
            size = 0
            with path.open("wb") as handle:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, "文件超过 500MB")
                    handle.write(chunk)
            with store.lock(project_id):
                project = store.load(project_id)
                result = footage.import_footage(
                    store, settings, project, path, filename=filename, replace_shots=replace_shots
                )
                if project.video_type is None:
                    project.video_type = "footage"
                store.save(project)
        return {
            "source_asset": result.source_asset,
            "shots": result.shot_ids,
            "segments": result.segments,
        }

    # ---- 背景音乐 -----------------------------------------------------------

    @api.post("/projects/{project_id}/bgm/candidates")
    def search_bgm(project_id: str, body: BgmSearch | None = None):
        """检索背景音乐候选（只返回元数据+试听地址，不下载文件）。"""
        _id(project_id)
        project = store.load(project_id)
        # 一次检索要几十秒，连点会各自查一遍。用和镜头生成相同的锁挡住。
        key = (project_id, BGM_KEY)
        with in_progress_lock:
            if key in in_progress:
                raise HTTPException(409, "背景音乐正在检索，请等它完成")
            in_progress[key] = {"mode": "music", "since": time.time()}
        try:
            candidates, query = music.search_bgm_candidates(
                settings, project, query=(body.query if body else "")
            )
            return {
                "query": query,
                "candidates": [
                    {
                        "provider": c.provider,
                        "remote_id": c.remote_id,
                        "url": c.url,
                        "preview_url": c.preview_url,
                        "title": c.title,
                        "duration": c.duration,
                        "author": c.author,
                        "meta": c.meta,
                    }
                    for c in candidates
                ],
            }
        finally:
            with in_progress_lock:
                in_progress.pop(key, None)

    @api.post("/projects/{project_id}/bgm/download")
    def download_bgm(project_id: str, body: BgmDownload):
        """下载选中的背景音乐候选并注册为素材，设为当前背景音乐，返回更新后的项目。"""
        _id(project_id)
        project = store.load(project_id)
        from shotwright.providers.base import MusicCandidate

        candidate = MusicCandidate(
            provider=body.provider,
            remote_id=body.remote_id,
            url=body.url,
            title=body.title,
            duration=body.duration,
            author=body.author,
            meta=body.meta,
        )
        asset = music.download_candidate(store, settings, project, candidate)

        def change(latest: Project) -> None:
            latest.assets.setdefault(asset.id, asset)
            latest.bgm.asset_id = asset.id
            if asset.id not in (latest.bgm.candidates or []):
                latest.bgm.candidates = [*(latest.bgm.candidates or []), asset.id]

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/sfx/ambience")
    def search_ambience(project_id: str, body: AmbienceSearch | None = None):
        """自动配环境声：用音乐库按场景检索一段环境声，下载后设为本项目的环境声。"""
        project = store.load(_id(project_id))
        query = (body.query if body else "").strip() or music.ambience_query(project)
        added, used = music.search_bgm(store, settings, project, query=query, limit=1)

        def change(latest: Project) -> None:
            for asset_id in added:
                latest.assets.setdefault(asset_id, project.assets[asset_id])
            latest.sfx.ambience = added[0]
            latest.sfx.ambience_query = used

        return _mutate(project_id, change)

    # ---- 费用预估 ------------------------------------------------------------

    @api.get("/projects/{project_id}/cost-estimate")
    def get_cost_estimate(project_id: str, quality: Literal["draft", "final"] = "final"):
        """估算渲染本项目的费用（元），不实际触发任何 AI 调用。草稿档按草稿分辨率估算。"""
        project = store.load(_id(project_id))
        return cost_service.estimate_render_cost(project, settings, quality).to_dict()

    @api.get("/platforms")
    def list_platforms():
        """交付平台规格：推荐画面比例和安全区（界面用来画安全区参考线）。"""
        return [
            {"id": p.id, "name": p.name, "name_en": p.name_en, "aspects": list(p.aspects), "safe": asdict(p.safe)}
            for p in platforms.PLATFORMS.values()
        ]

    @api.get("/projects/{project_id}/checkup")
    def get_checkup(project_id: str, quality: Literal["draft", "final"] = "final"):
        """渲染前体检：列出缺画面、素材太短、分辨率不足、文字重叠、超出费用上限等问题。只读。"""
        project = store.load(_id(project_id))
        return {"issues": checkup.to_dicts(checkup.check_project(project, settings, quality=quality))}

    # ---- 渲染 ---------------------------------------------------------------

    @api.post("/projects/{project_id}/renders", status_code=202)
    def start_render(project_id: str, body: ChargeConsent | None = None):
        """``confirm_charge: true`` 时，没有画面的 AI 镜头会提交收费生成；否则改用素材检索。"""
        if not store.exists(_id(project_id)):
            raise ProjectNotFound(project_id)
        if body and body.confirm_charge and readiness.generation_locked(store.load(project_id)):
            raise HTTPException(409, readiness.LOCKED_MESSAGE)
        return jobs.submit(
            project_id,
            allow_paid=bool(body and body.confirm_charge),
            quality=body.quality if body else "final",
        ).to_dict()

    @api.get("/projects/{project_id}/renders")
    def list_renders(project_id: str):
        directory = store.project_dir(_id(project_id)) / "renders"
        finished = []
        if directory.is_dir():
            for child in directory.iterdir():
                data = read_json(child / "render.json")
                if data:
                    finished.append({**data, "created_at": child.stat().st_mtime})
        finished.sort(key=lambda item: item["created_at"], reverse=True)
        return {"jobs": [j.to_dict() for j in jobs.list(project_id)], "renders": finished}

    @api.get("/renders/{job_id}")
    def get_job(job_id: str):
        job = jobs.get(_id(job_id))
        if job is None:
            raise HTTPException(404, "任务不存在")
        return job.to_dict()

    @api.post("/renders/{job_id}/cancel")
    def cancel_job(job_id: str):
        if not jobs.cancel(_id(job_id)):
            raise HTTPException(409, "任务已结束或不存在")
        return {"ok": True}

    @api.get("/projects/{project_id}/renders/{render_id}/{name}")
    def render_file(project_id: str, render_id: str, name: str):
        if name not in ("video.mp4", "subtitles.srt"):
            raise HTTPException(404, "文件不存在")
        path = store.project_dir(_id(project_id)) / "renders" / _id(render_id) / name
        if not path.is_file():
            raise HTTPException(404, "文件不存在")
        media_type = "video/mp4" if name.endswith(".mp4") else "application/x-subrip"
        return FileResponse(path, media_type=media_type, filename=f"shotwright-{render_id}-{name}")

    app.include_router(api)

    if WEB_DIST.is_dir():
        @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
        def unknown_api(path: str):
            # 不存在的接口返回 JSON 404，不要落到下面的静态文件上（那里只会返回 405 Method Not Allowed）。
            raise HTTPException(404, "接口不存在")

        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

    return app
