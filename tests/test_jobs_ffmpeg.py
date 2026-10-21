"""任务表清理与 ffmpeg 取消。"""

import threading
from concurrent.futures import Executor

import pytest

from shotwright.api.jobs import MAX_FINISHED_JOBS, JobManager
from shotwright.media import ffmpeg


class _NoopExecutor(Executor):
    """不真正执行，只保留任务记录，便于检查任务表的清理。"""

    def submit(self, fn, *args, **kwargs):
        class _Future:
            def result(self):
                return None

        return _Future()


def test_finished_jobs_are_pruned(store, settings):
    jobs = JobManager(store, settings, executor=_NoopExecutor())
    for index in range(MAX_FINISHED_JOBS + 5):
        job = jobs.submit("p")
        job.state = "done"
        job.created_at = float(index)
    # 最后一次 submit 时最后那个任务还是 queued，所以补一次清理（真实流程里由 _run 收尾时触发）。
    with jobs._lock:
        jobs._prune()
    finished = [job for job in jobs._jobs.values() if job.state == "done"]
    assert len(finished) == MAX_FINISHED_JOBS
    assert all(job.created_at >= 5 for job in finished)


def test_running_jobs_are_never_pruned(store, settings):
    jobs = JobManager(store, settings, executor=_NoopExecutor())
    important = jobs.submit("p")
    important.state = "running"
    important.created_at = 0.0
    for index in range(MAX_FINISHED_JOBS + 3):
        job = jobs.submit("p")
        job.state = "done"
        job.created_at = float(index + 1)
    assert jobs.get(important.id) is not None


def test_ffmpeg_cancel_terminates_quickly(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ffmpeg.MediaCancelled):
        ffmpeg.run_ffmpeg(
            ["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "60", str(tmp_path / "out.mp4")],
            cancel=cancel,
        )
    assert not (tmp_path / "out.mp4").exists()


def test_ffmpeg_runs_normally_without_cancel(tmp_path):
    out = tmp_path / "ok.mp4"
    ffmpeg.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10", "-t", "1", str(out)])
    assert out.is_file() and out.stat().st_size > 0
