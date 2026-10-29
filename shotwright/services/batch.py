"""一键生成：把还没有画面的 AI 镜头和审片时标记「重做」的镜头排队，在后台依次生成。

这里只管挑镜头、算费用和记录进度；真正的生成复用单镜头生成的接口（见 ``api/app.py``），
所以扣费确认、费用上限、「同一镜头不重复提交」这些规则和手动生成完全一致。

进度只存在内存里：服务重启后队列不再继续，已经提交的单个生成仍按原来的方式恢复。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from shotwright.config import Settings
from shotwright.core.models import Project, Shot
from shotwright.services import cost

BatchState = Literal["running", "done", "cancelled", "stopped"]


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


_jobs: dict[str, BatchJob] = {}
_lock = threading.Lock()


class BatchRunning(RuntimeError):
    pass


def get(project_id: str) -> BatchJob | None:
    with _lock:
        return _jobs.get(project_id)


def start(project_id: str, shot_ids: list[str], quality: str, estimate: float) -> BatchJob:
    with _lock:
        existing = _jobs.get(project_id)
        if existing is not None and existing.state == "running":
            raise BatchRunning("正在一键生成，请等它完成或先停止")
        job = BatchJob(project_id, list(shot_ids), quality, round(estimate, 4))
        _jobs[project_id] = job
        return job


def cancel(project_id: str) -> BatchJob | None:
    """请求停止：正在生成的那个镜头会做完，后面的不再提交。"""
    with _lock:
        job = _jobs.get(project_id)
        if job is not None and job.state == "running":
            job.cancel_requested = True
        return job


def finish(job: BatchJob) -> None:
    with _lock:
        job.current = None
        if job.stopped_reason:
            job.state = "stopped"
        else:
            job.state = "cancelled" if job.cancel_requested else "done"
        job.finished_at = time.time()


def clear() -> None:
    """测试用：清空所有记录。"""
    with _lock:
        _jobs.clear()
