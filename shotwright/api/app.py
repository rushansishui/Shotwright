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
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from shotwright import __version__
from shotwright.api import errors
from shotwright.api.jobs import JobManager
from shotwright.api.schemas import (
    AddShot,
    BgmDownload,
    BgmSearch,
    ChargeConsent,
    CreateProject,
    CreateVersion,
    ReorderShots,
    UpdateProject,
    UpdateShot,
)
from shotwright.config import ROOT_DIR, Settings, load_settings
from shotwright.core import languages
from shotwright.core.models import Project, Shot, ShotVisual, VoiceSettings, validate_id
from shotwright.core.store import ProjectNotFound, ProjectStore, read_json
from shotwright.logs import setup_logging
from shotwright.media import ffmpeg
from shotwright.providers import registry
from shotwright.providers.base import ChargeConfirmationRequired, ProviderError
from shotwright.providers.tts.edge import VOICES
from shotwright.services import localize, music, storyboard, visuals

log = logging.getLogger("shotwright.api")

MAX_UPLOAD = 500 * 1024 * 1024
WEB_DIST = ROOT_DIR / "web" / "dist"
BGM_KEY = "__bgm__"
"""in_progress 里用这个伪镜头 id 表示「背景音乐检索中」。"""


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
        yield
        jobs.shutdown()

    app = FastAPI(title="Shotwright", version=__version__, lifespan=lifespan)
    app.state.settings, app.state.store, app.state.jobs = settings, store, jobs
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
                        {"key": f.key, "label": f.label, "required": f.required, "secret": f.secret}
                        for f in spec.fields
                    ],
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

    # ---- 项目 ---------------------------------------------------------------

    @api.get("/projects")
    def list_projects():
        return [
            {
                "id": p.id,
                "title": p.title or p.subject or "未命名项目",
                "shots": len(p.shots),
                "aspect": p.canvas.aspect,
                "language": languages.normalize(p.language),
                "group": p.group or p.id,
                "updated_at": p.updated_at,
            }
            for p in store.list()
        ]

    @api.post("/projects", status_code=201)
    def create_project(body: CreateProject):
        lang = languages.get(body.language)
        project = Project(title=body.title, subject=body.subject, target_seconds=body.duration)
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
            title, shots = storyboard.generate_storyboard(
                llm,
                body.subject.strip(),
                language=lang.prompt_name,
                shots=body.shots,
                duration=body.duration,
            )
            project.title = project.title or title
            project.shots = shots
        elif body.mode == "split":
            if not body.script.strip():
                raise HTTPException(422, "请填写视频文案")
            project.shots = storyboard.split_script(body.script)
        project.title = project.title or body.subject[:40] or "未命名项目"
        return store.create(project)

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
            for name in ("title", "subject", "target_seconds", "canvas", "voice", "subtitle", "bgm"):
                value = getattr(body, name)
                if value is not None:
                    setattr(project, name, value)
            if project.bgm.asset_id and project.bgm.asset_id not in project.assets:
                raise ValueError("背景音乐素材不存在")

        return _mutate(project_id, change)

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

    @api.patch("/projects/{project_id}/shots/{shot_id}")
    def update_shot(project_id: str, shot_id: str, body: UpdateShot):
        def change(project: Project) -> None:
            shot = project.shot(_id(shot_id))
            if body.text is not None:
                shot.text = body.text
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
                if patch.get("first_frame") and project.assets[patch["first_frame"]].kind != "image":
                    raise ValueError("首帧必须是图片")
                if "terms" in patch:
                    patch["terms"] = [t.strip()[:80] for t in patch["terms"] if t.strip()]
                if patch.get("selected") and patch["selected"] not in shot.visual.candidates:
                    patch["candidates"] = [*shot.visual.candidates, patch["selected"]][
                        -visuals.MAX_CANDIDATES :
                    ]
                mode = patch.pop("mode", None)
                if mode is not None:
                    # 切换来源时自动选中该来源的素材（显式传了 selected 时以它为准）。
                    visuals.switch_mode(project, shot, mode)
                shot.visual = shot.visual.model_copy(update=patch)
                ShotVisual.model_validate(shot.visual.model_dump())
            shot.status, shot.error = "draft", None

        return _mutate(project_id, change)

    @api.delete("/projects/{project_id}/shots/{shot_id}")
    def delete_shot(project_id: str, shot_id: str):
        def change(project: Project) -> None:
            project.shots.remove(project.shot(_id(shot_id)))

        return _mutate(project_id, change)

    # 正在检索或生成的镜头：页面刷新后仍能显示"生成中"，并拒绝重复提交。
    in_progress: dict[tuple[str, str], dict] = {}
    in_progress_lock = threading.Lock()

    @api.get("/projects/{project_id}/in-progress")
    def shots_in_progress(project_id: str):
        _id(project_id)
        now = time.time()
        with in_progress_lock:
            return {
                shot_id: {"mode": info["mode"], "seconds": round(now - info["since"], 1)}
                for (pid, shot_id), info in in_progress.items()
                if pid == project_id
            }

    @api.post("/projects/{project_id}/shots/{shot_id}/candidates")
    def refresh_candidates(project_id: str, shot_id: str, body: ChargeConsent | None = None):
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
        confirmed = bool(body and body.confirm_charge)
        key = (project_id, shot_id)
        with in_progress_lock:
            if key in in_progress:
                # 同一个镜头不允许重复提交，避免连点造成重复扣费。
                raise HTTPException(409, "这个镜头正在生成，请等它完成")
            in_progress[key] = {"mode": shot.visual.mode, "since": time.time()}
        generated = shot.visual.mode in ("ai", "image")

        def change(latest: Project) -> None:
            for asset_id, asset in project.assets.items():
                latest.assets.setdefault(asset_id, asset)
            target = latest.shot(shot_id)
            target.visual.candidates = shot.visual.candidates
            if generated or target.visual.selected is None or target.visual.selected not in latest.assets:
                # 刚生成的画面花了钱，直接选中它。
                target.visual.selected = shot.visual.selected

        try:
            visuals.refresh_candidates(store, settings, project, shot, confirm_charge=confirmed)
            return _mutate(project_id, change)
        finally:
            # 保存之后再移除，页面看到"完成"时就能读到新素材。
            with in_progress_lock:
                in_progress.pop(key, None)

    # ---- 素材 ---------------------------------------------------------------

    @api.post("/projects/{project_id}/assets", status_code=201)
    def upload_asset(project_id: str, file: UploadFile = File(...)):
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
                asset = visuals.add_upload(store, project, path, filename=filename)
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

    # ---- 渲染 ---------------------------------------------------------------

    @api.post("/projects/{project_id}/renders", status_code=202)
    def start_render(project_id: str, body: ChargeConsent | None = None):
        """``confirm_charge: true`` 时，没有画面的 AI 镜头会提交收费生成；否则改用素材检索。"""
        if not store.exists(_id(project_id)):
            raise ProjectNotFound(project_id)
        return jobs.submit(project_id, allow_paid=bool(body and body.confirm_charge)).to_dict()

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
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

    return app
