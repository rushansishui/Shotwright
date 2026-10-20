"""渲染任务队列（进程内）。

同一时间只渲染一个任务，其余排队；WebUI、API、CLI 提交的任务都在这里。
多进程或多机部署时可以换成 Redis 队列，接口保持不变。
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Literal

from shotwright.config import Settings
from shotwright.core.ids import new_id
from shotwright.core.store import ProjectStore
from shotwright.pipeline.render import RenderCancelled, Renderer

log = logging.getLogger(__name__)

JobState = Literal["queued", "running", "done", "failed", "cancelled"]

MAX_FINISHED_JOBS = 200
"""保留的已结束任务数；超过时按创建时间丢弃最旧的，避免长跑服务内存持续增长。"""


@dataclass
class Job:
    id: str
    project_id: str
    state: JobState = "queued"
    progress: float = 0.0
    message: str = "排队中"
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None
    result: dict | None = None
    allow_paid: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class JobManager:
    def __init__(self, store: ProjectStore, settings: Settings, executor: Executor | None = None):
        self.store = store
        self.settings = settings
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")
        self._jobs: dict[str, Job] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def submit(self, project_id: str, *, allow_paid: bool = False) -> Job:
        job = Job(id=new_id(), project_id=project_id, allow_paid=allow_paid)
        with self._lock:
            self._jobs[job.id] = job
            self._cancel[job.id] = threading.Event()
            self._prune()
        self._executor.submit(self._run, job.id)
        return job

    def _prune(self) -> None:
        """丢弃最旧的已结束任务，只保留 :data:`MAX_FINISHED_JOBS` 个。调用方需持有锁。"""
        finished = [job for job in self._jobs.values() if job.state in ("done", "failed", "cancelled")]
        excess = len(finished) - MAX_FINISHED_JOBS
        if excess <= 0:
            return
        for job in sorted(finished, key=lambda item: item.created_at)[:excess]:
            self._jobs.pop(job.id, None)
            self._cancel.pop(job.id, None)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, project_id: str | None = None) -> list[Job]:
        with self._lock:
            jobs = [j for j in self._jobs.values() if project_id is None or j.project_id == project_id]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            event = self._cancel.get(job_id)
            job = self._jobs.get(job_id)
        if event is None or job is None or job.state in ("done", "failed", "cancelled"):
            return False
        event.set()
        if job.state == "queued":
            job.state, job.message, job.finished_at = "cancelled", "已取消", time.time()
        return True

    def _run(self, job_id: str) -> None:
        job = self._jobs[job_id]
        cancel = self._cancel[job_id]
        if cancel.is_set():
            return
        job.state, job.message = "running", "开始渲染"
        log.info(
            "渲染任务 %s 开始：项目 %s，允许收费生成 %s",
            job.id,
            job.project_id,
            "是" if job.allow_paid else "否",
        )

        def progress(value: float, message: str) -> None:
            job.progress, job.message = round(value, 3), message

        try:
            result = Renderer(
                self.store, self.settings, progress=progress, cancel=cancel, allow_paid=job.allow_paid
            ).render(job.project_id, render_id=job.id)
        except RenderCancelled:
            job.state, job.message = "cancelled", "已取消"
            log.info("渲染任务 %s 已取消", job.id)
        except Exception as exc:  # noqa: BLE001 - 失败原因展示给用户
            job.state, job.error, job.message = "failed", str(exc) or type(exc).__name__, "渲染失败"
            log.exception("渲染任务 %s 失败", job.id)
        else:
            job.state, job.progress, job.message, job.result = "done", 1.0, "完成", result.to_dict()
            reused = sum(1 for s in result.stages if s.reused)
            log.info(
                "渲染任务 %s 完成：%.1f 秒成片，复用缓存 %d/%d，提示 %d 条",
                job.id,
                result.duration,
                reused,
                len(result.stages),
                len(result.warnings),
            )
            for warning in result.warnings:
                log.warning("渲染任务 %s：%s", job.id, warning)
        finally:
            job.finished_at = time.time()
            with self._lock:
                self._prune()

    def shutdown(self) -> None:
        for event in self._cancel.values():
            event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
