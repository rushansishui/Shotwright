"""画面生成：检索 / 生成候选、单镜头改写、一键生成、补全提示词。"""

from __future__ import annotations

import threading
import time
from typing import Literal

from fastapi import APIRouter, HTTPException

from shotwright.api.context import ApiContext, log
from shotwright.api.schemas import CandidatesRequest, ExpandRequest, GenerateAll, RewriteShot
from shotwright.core.models import Project
from shotwright.core.store import ProjectNotFound
from shotwright.providers import registry
from shotwright.providers.base import BudgetExceeded, ChargeConfirmationRequired
from shotwright.services import batch as batch_service
from shotwright.services import readiness, rewrite, visuals


def register(api: APIRouter, ctx: ApiContext) -> None:
    store = ctx.store
    settings = ctx.settings
    _id = ctx.id
    _mutate = ctx.mutate
    _clear_pending = ctx.clear_pending
    in_progress = ctx.in_progress
    in_progress_lock = ctx.in_progress_lock

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
        if (body.edit_selected or shot.visual.mode in ("ai", "image")) and readiness.generation_locked(
            project
        ):
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
    def generate_all_status(
        project_id: str, quality: Literal["draft", "final"] = "final", only_redo: bool = False
    ):
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
                            rewrite_shot(
                                project_id, shot_id, RewriteShot(instruction=shot.visual.review_note[:500])
                            )
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
                    batch_service.progress(job)
                except BudgetExceeded as exc:
                    job.failed.append({"shot_id": shot_id, "error": str(exc)})
                    job.stopped_reason = str(exc)
                    batch_service.progress(job)
                    break
                except HTTPException as exc:
                    job.failed.append({"shot_id": shot_id, "error": str(exc.detail)})
                    batch_service.progress(job)
                except Exception as exc:  # noqa: BLE001 - 一个镜头失败不影响后面的
                    log.warning("一键生成：镜头 %s 失败：%s", shot_id, exc)
                    job.failed.append({"shot_id": shot_id, "error": str(exc) or type(exc).__name__})
                    batch_service.progress(job)
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
