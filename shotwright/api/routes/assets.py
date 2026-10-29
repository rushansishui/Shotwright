"""素材、原片导入、背景音乐与音效。"""

from __future__ import annotations

import mimetypes
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from shotwright.api.context import BGM_KEY, MAX_UPLOAD, ApiContext
from shotwright.api.schemas import AmbienceSearch, BgmDownload, BgmSearch
from shotwright.core.models import Project
from shotwright.core.store import ProjectNotFound
from shotwright.services import footage, music, visuals


def register(api: APIRouter, ctx: ApiContext) -> None:
    store = ctx.store
    settings = ctx.settings
    _id = ctx.id
    _mutate = ctx.mutate
    in_progress = ctx.in_progress
    in_progress_lock = ctx.in_progress_lock

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
                asset = visuals.add_upload(store, project, path, filename=filename, prefer_audio=as_audio)
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
