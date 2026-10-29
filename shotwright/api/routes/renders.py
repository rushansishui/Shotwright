"""渲染任务和成片下载。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from shotwright.api.context import ApiContext
from shotwright.api.schemas import ChargeConsent
from shotwright.core.store import ProjectNotFound, read_json
from shotwright.services import readiness


def register(api: APIRouter, ctx: ApiContext) -> None:
    store = ctx.store
    jobs = ctx.jobs
    _id = ctx.id

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
