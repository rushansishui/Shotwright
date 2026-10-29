"""一键生成：把还没有画面的 AI 镜头和审片时标记「重做」的镜头排队，在后台依次生成。

这里只管挑镜头、算费用和记录进度；真正的生成复用单镜头生成的接口（见 ``api/app.py``），
所以扣费确认、费用上限、「同一镜头不重复提交」这些规则和手动生成完全一致。

进度同时写进项目的 ``batch.json``：服务重启后队列不再继续，但页面能看出上次跑到哪、
哪些镜头已经生成过（那些已经扣过费，不会重复生成），状态标成 ``interrupted``。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from shotwright.config import Settings
from shotwright.core.models import Project, Shot
from shotwright.core.store import ProjectStore, read_json, write_json
from shotwright.services import cost

log = logging.getLogger("shotwright.batch")

BatchState = Literal["running", "done", "cancelled", "stopped", "interrupted"]

BATCH_FILE = "batch.json"
"""一键生成的进度写在项目目录下这个文件里（很小，不进 project.json）。"""


def needs_generation(project: Project, shot: Shot) -> str | None:
    """镜头为什么要生成：missing 没有可用画面，redo 审片标记重做；不需要时返回 None。"""
    visual = shot.visual
    if visual.mode not in ("ai", "image") or visual.locked:
        return None
    if visual.review == "redo":
        return "redo"
    if not visual.selected or visual.selected not in project.assets:
        return "missing"
    return None


def plan(project: Project, settings: Settings, quality: str = "final", only_redo: bool = False) -> dict:
    """要生成哪些镜头、各自大约多少钱。"""
    items = []
    for index, shot in enumerate(project.shots):
        reason = needs_generation(project, shot)
        if reason is None or (only_redo and reason != "redo"):
            continue
        amount, note = cost.visual_generation_cost(project, shot, settings, quality)
        kind = "video_gen" if shot.visual.mode == "ai" else "image_gen"
        provider_id = shot.visual.provider or settings.default_provider(kind)
        items.append(
            {
                "shot_id": shot.id,
                "shot_index": index,
                "reason": reason,
                "mode": shot.visual.mode,
                "provider": provider_id,
                "paid": _paid(kind, provider_id),
                "cost": round(amount, 4),
                "note": note,
                "review_note": shot.visual.review_note if reason == "redo" else "",
            }
        )
    return {
        "items": items,
        "total": round(sum(item["cost"] for item in items), 4),
        "paid": any(item["paid"] for item in items),
        "spent": round(project.cost_credits, 4),
        "budget": project.budget,
        "currency": "CNY",
    }


def _paid(kind: str, provider_id: str | None) -> bool:
    """是不是收费服务；查不到时按收费处理，宁可多问一次。"""
    from shotwright.providers import registry

    try:
        return bool(registry.spec(kind, provider_id or "").paid)
    except Exception:  # noqa: BLE001
        return True


@dataclass
class BatchJob:
    project_id: str
    shot_ids: list[str]
    quality: str = "final"
    estimate: float = 0.0
    state: BatchState = "running"
    current: str | None = None
    done: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    stopped_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "total": len(self.shot_ids),
            "shot_ids": list(self.shot_ids),
            "quality": self.quality,
            "estimate": self.estimate,
            "current": self.current,
            "done": list(self.done),
            "failed": list(self.failed),
            "skipped": list(self.skipped),
            "cancel_requested": self.cancel_requested,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stopped_reason": self.stopped_reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BatchJob:
        return cls(
            project_id=str(data.get("project_id") or ""),
            shot_ids=[str(item) for item in data.get("shot_ids") or []],
            quality=str(data.get("quality") or "final"),
            estimate=float(data.get("estimate") or 0.0),
            state=data.get("state") or "running",
            current=data.get("current"),
            done=[str(item) for item in data.get("done") or []],
            failed=list(data.get("failed") or []),
            skipped=[str(item) for item in data.get("skipped") or []],
            cancel_requested=bool(data.get("cancel_requested")),
            started_at=float(data.get("started_at") or time.time()),
            finished_at=data.get("finished_at"),
            stopped_reason=str(data.get("stopped_reason") or ""),
        )


_jobs: dict[str, BatchJob] = {}
_lock = threading.Lock()
_store: ProjectStore | None = None
"""调过 :func:`configure` 之后才落盘；没配置时只放内存（测试和命令行用）。"""


def configure(store: ProjectStore) -> None:
    """指定把进度写在哪个存储里，启动时调用一次。"""
    global _store
    _store = store


def _path(project_id: str) -> Path | None:
    if _store is None:
        return None
    try:
        return _store.cache_dir(project_id, "batch") / BATCH_FILE
    except (ValueError, OSError):
        return None


def _save(job: BatchJob) -> None:
    """把进度写进项目目录。写失败只记日志：进度丢了不能拖垮正在跑的生成。"""
    path = _path(job.project_id)
    if path is None:
        return
    try:
        write_json(path, {"project_id": job.project_id, **job.to_dict()})
    except OSError as exc:
        log.warning("一键生成进度保存失败：%s", exc)


def _load(project_id: str) -> BatchJob | None:
    path = _path(project_id)
    if path is None:
        return None
    data = read_json(path)
    if not data or not data.get("shot_ids"):
        return None
    return BatchJob.from_dict(data)


def _restore(project_id: str) -> BatchJob | None:
    """读回落盘的进度；上次跑着就被重启时标成 interrupted。"""
    job = _load(project_id)
    if job is None:
        return None
    if job.state == "running":
        # 进程重启后后台线程已经没了：任务不会继续，但已生成的镜头不用重做。
        job.state = "interrupted"
        job.current = None
        job.finished_at = job.finished_at or time.time()
        job.stopped_reason = (
            job.stopped_reason or "服务重启，一键生成已中断；已生成的镜头保留，可以再点一次继续"
        )
        _save(job)
    return job


class BatchRunning(RuntimeError):
    pass


def get(project_id: str) -> BatchJob | None:
    with _lock:
        job = _jobs.get(project_id)
        if job is not None:
            return job
    job = _restore(project_id)
    if job is not None:
        with _lock:
            _jobs.setdefault(project_id, job)
            return _jobs[project_id]
    return None


def start(project_id: str, shot_ids: list[str], quality: str, estimate: float) -> BatchJob:
    with _lock:
        existing = _jobs.get(project_id)
        if existing is None:
            # 内存里没有时先看看盘上是不是还有上次的任务，免得重启后又开一份。
            existing = _restore(project_id)
            if existing is not None:
                _jobs[project_id] = existing
        if existing is not None and existing.state == "running":
            raise BatchRunning("正在一键生成，请等它完成或先停止")
        job = BatchJob(project_id, list(shot_ids), quality, round(estimate, 4))
        _jobs[project_id] = job
        _save(job)
        return job


def cancel(project_id: str) -> BatchJob | None:
    """请求停止：正在生成的那个镜头会做完，后面的不再提交。"""
    with _lock:
        job = _jobs.get(project_id)
        if job is not None and job.state == "running":
            job.cancel_requested = True
            _save(job)
        return job


def progress(job: BatchJob) -> None:
    """每跑完一个镜头存一次：重启后能看出跑到哪了。"""
    with _lock:
        _save(job)


def finish(job: BatchJob) -> None:
    with _lock:
        job.current = None
        if job.stopped_reason:
            job.state = "stopped"
        else:
            job.state = "cancelled" if job.cancel_requested else "done"
        job.finished_at = time.time()
        _save(job)


def clear() -> None:
    """测试用：清空所有记录。"""
    global _store
    with _lock:
        _jobs.clear()
        _store = None
