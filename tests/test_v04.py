"""v0.4 新功能的回归测试：费用估算、转场时间轴、改画面、Host 校验、原片导入、任务恢复、元数据。"""


from conftest import FakeVideoGen, make_clip
from fastapi.testclient import TestClient

from shotwright.api.app import create_app
from shotwright.api.jobs import JobManager
from shotwright.core.models import (
    PendingTask,
    Project,
    Shot,
    ShotVisual,
    VoiceSettings,
)
from shotwright.core.store import ProjectStore
from shotwright.providers import registry
from shotwright.providers.base import ProviderSpec
from shotwright.services import visuals

# ---- 测试用的服务 -----------------------------------------------------------


class FakeEditVideo(FakeVideoGen):
    """支持视频转视频的假服务，和 FakeVideoGen 共用调用记录。"""


class FakeResumable(FakeVideoGen):
    resumed: list[str] = []

    def resume(self, task_id, *, output):
        FakeResumable.resumed.append(task_id)
        return make_clip(output.with_suffix(".mp4"), pattern="rgbtestsrc")


registry.register(
    ProviderSpec(kind="video_gen", id="fakeedit", name="FakeEdit", paid=True, video_to_video=True), FakeEditVideo
)
registry.register(ProviderSpec(kind="video_gen", id="fakeresume", name="FakeResume", paid=True), FakeResumable)


def _project(store, *, shots=None, **kwargs) -> Project:
    shots = shots or [
        Shot(text="第一个镜头，介绍主题。", visual=ShotVisual(mode="color", color="#AA0000")),
        Shot(text="第二个镜头讲细节。", visual=ShotVisual(mode="color", color="#00AA00")),
        Shot(text="第三个镜头收尾。", visual=ShotVisual(mode="color", color="#0000AA")),
    ]
    return store.create(Project(voice=VoiceSettings(provider="silent"), shots=shots, **kwargs))


def _client(settings, store=None):
    store = store or ProjectStore(settings.storage_dir)
    return TestClient(create_app(settings, job_manager=JobManager(store, settings)))


# ---- Host / Origin 校验 -----------------------------------------------------


def test_rejects_unknown_host_and_cross_site_origin(settings):
    with _client(settings) as client:
        assert client.get("/api/v2/health").status_code == 200
        assert client.get("/api/v2/health", headers={"host": "evil.example"}).status_code == 403
        assert client.get("/api/v2/health", headers={"host": "127.0.0.1:8000"}).status_code == 200
        r = client.post(
            "/api/v2/projects",
            json={"mode": "split", "script": "一句。"},
            headers={"origin": "https://evil.example"},
        )
        assert r.status_code == 403
        r = client.post(
            "/api/v2/projects",
            json={"mode": "split", "script": "一句。"},
            headers={"origin": "http://localhost:5173"},
        )
        assert r.status_code == 201, r.text


# ---- 重启后恢复收费任务 -----------------------------------------------------


def test_pending_task_is_resumed_without_resubmitting(store, settings):
    project = _project(store, shots=[Shot(text="一句。", visual=ShotVisual(mode="ai", prompt="sea", provider="fakeresume"))])
    project.shots[0].visual.pending = PendingTask(provider="fakeresume", task_id="task-42", kind="video_gen")
    store.save(project)
    FakeResumable.resumed.clear()
    before = len(FakeVideoGen.calls)
    added = visuals.resume_pending(store, settings, project, project.shots[0])
    assert FakeResumable.resumed == ["task-42"]
    assert len(FakeVideoGen.calls) == before
    assert project.shots[0].visual.pending is None and project.shots[0].visual.selected == added[0]
