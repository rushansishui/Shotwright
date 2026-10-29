"""HTTP API（/api/v2）与前端静态文件。

启动：``shotwright serve``，或 ``uvicorn shotwright.api.app:create_app --factory``。
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from shotwright import __version__
from shotwright.api import errors
from shotwright.api.context import ApiContext
from shotwright.api.jobs import JobManager
from shotwright.api.routes import assets, generation, projects, renders, shots, system, transcripts
from shotwright.api.routes import settings as app_settings
from shotwright.config import ROOT_DIR, Settings, load_settings
from shotwright.core import limits
from shotwright.core.store import ProjectNotFound, ProjectStore
from shotwright.logs import setup_logging
from shotwright.media import ffmpeg
from shotwright.providers.base import ChargeConfirmationRequired, ProviderError
from shotwright.services import batch as batch_service
from shotwright.services import visuals

log = logging.getLogger("shotwright.api")

WEB_DIST = ROOT_DIR / "web" / "dist"


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
    # 一键生成的进度写在项目目录里，重启后能看出上次跑到哪。
    batch_service.configure(store)
    # 同时在跑的收费生成和本地识别各有上限，几个项目一起跑时不会一起猛打接口。
    limits.configure(ai=settings.ai_concurrency, asr=settings.asr_concurrency)
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
                                item
                                for item in target.visual.takes
                                if item.asset_id in target.visual.candidates
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
            log.warning("拒绝来自 Origin=%s 的 %s 请求", request.headers.get("origin", ""), request.method)
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
    ctx = ApiContext(settings=settings, store=store, jobs=jobs)
    for module in (system, app_settings, projects, shots, transcripts, generation, assets, renders):
        module.register(api, ctx)

    app.include_router(api)

    @app.api_route(
        "/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False
    )
    def unknown_api(path: str):
        # 不存在的接口统一返回带说明的 JSON 404；有前端页面时也不会落到下面的静态文件上
        # （那里只会返回 405 Method Not Allowed）。没有构建前端（例如 CI）时行为一致。
        raise HTTPException(404, "接口不存在")

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

    return app
