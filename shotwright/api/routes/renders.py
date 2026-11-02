"""渲染任务和成片下载。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from shotwright.api.context import ApiContext
from shotwright.api.schemas import RenderRequest
from shotwright.core.store import ProjectNotFound, read_json
from shotwright.providers import registry
from shotwright.providers.base import ProviderError
from shotwright.services import publish, readiness


def register(api: APIRouter, ctx: ApiContext) -> None:
    store = ctx.store
    jobs = ctx.jobs
    _id = ctx.id

    # ---- 渲染 ---------------------------------------------------------------

    @api.post("/projects/{project_id}/renders", status_code=202)
    def start_render(project_id: str, body: RenderRequest | None = None):
        """``confirm_charge: true`` 时，没有画面的 AI 镜头会提交收费生成；否则改用素材检索。

        ``sample: true`` 是整片样片：一律不收费、用草稿档，先把整片跑一遍确认节奏和文案。
        """
        if not store.exists(_id(project_id)):
            raise ProjectNotFound(project_id)
        body = body or RenderRequest()
        if body.confirm_charge and not body.sample and readiness.generation_locked(store.load(project_id)):
            raise HTTPException(409, readiness.LOCKED_MESSAGE)
        return jobs.submit(
            project_id,
            allow_paid=body.confirm_charge and not body.sample,
            quality=body.quality,
            sample=body.sample,
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

    # ---- 发布包 -------------------------------------------------------------

    def _latest_render(project_id: str) -> dict | None:
        """最近一次渲染的结果（render.json），按成片文件的修改时间取最新的。"""
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

    def _text_llm():
        """配了文字模型就用它拟标题和话题；没配时发布包用兜底文案。"""
        try:
            return registry.create("llm", None, ctx.settings)
        except (ProviderError, KeyError, ValueError):
            return None

    @api.get("/projects/{project_id}/publish")
    def get_publish(project_id: str):
        """发布包里会用到的文字（标题、简介、话题标签），先给界面看一眼，不打包。"""
        project = store.load(_id(project_id))
        latest = _latest_render(project.id)
        duration = float((latest or {}).get("duration") or 0.0)
        return publish.plan_pack(project, llm=_text_llm(), duration=duration).to_dict()

    @api.get("/projects/{project_id}/publish.zip")
    def download_publish(project_id: str):
        """发布包：成片、封面、字幕和 publish.json 打成一个 zip。用最近一次成片。"""
        project = store.load(_id(project_id))
        latest = _latest_render(project.id)
        if latest is None:
            raise HTTPException(404, "还没有成片，先渲染一次")
        directory = store.project_dir(project.id) / "renders" / latest["render_id"]
        video = directory / "video.mp4"
        if not video.is_file():
            raise HTTPException(404, "成片文件不存在")
        subtitles = directory / "subtitles.srt"
        pack = publish.plan_pack(project, llm=_text_llm(), duration=float(latest.get("duration") or 0.0))
        temp = Path(tempfile.mkdtemp(prefix="shotwright-publish-"))
        target = temp / publish.pack_name(pack)
        publish.build_pack(
            project,
            video=video,
            subtitles=subtitles if subtitles.is_file() else None,
            pack=pack,
            target=target,
        )
        return FileResponse(
            target,
            media_type="application/zip",
            filename=target.name,
            background=BackgroundTask(shutil.rmtree, temp, True),
        )
