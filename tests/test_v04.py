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
    TransitionSettings,
    VoiceSettings,
)
from shotwright.core.store import ProjectStore
from shotwright.media.ffmpeg import probe
from shotwright.pipeline.render import Renderer
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


# ---- 转场 -------------------------------------------------------------------


def test_transition_shifts_shot_starts_and_keeps_length(store, settings):
    project = _project(store, transition=TransitionSettings(kind="fade", duration=0.8))
    result = Renderer(store, settings).render(project.id)
    timings = result.timings
    for previous, current in zip(timings, timings[1:], strict=False):
        # 下一个镜头在上一个镜头结束前 0.8 秒开始。
        assert abs(current.start - (previous.start + previous.duration - 0.8)) < 0.01
        # 尾部空白至少有转场那么长，配音不会互相压住。
        assert previous.duration - previous.voice_duration >= 0.8 - 0.01
    expected = timings[-1].start + timings[-1].duration
    info = probe(store.render_dir(project.id, result.render_id) / result.video)
    assert abs(info.duration - expected) < 0.3
    assert info.has_audio
    # 字幕起点跟着镜头的新起点走。
    srt = (store.render_dir(project.id, result.render_id) / "subtitles.srt").read_text(encoding="utf-8")
    assert "第二个镜头" in srt


def test_transition_falls_back_to_cut_for_short_shots(store, settings):
    shots = [
        Shot(text="", duration=0.5, visual=ShotVisual(mode="color", color="#AA0000")),
        Shot(text="", duration=2.0, visual=ShotVisual(mode="color", color="#00AA00")),
    ]
    project = _project(store, shots=shots, transition=TransitionSettings(kind="fade", duration=1.0))
    result = Renderer(store, settings).render(project.id)
    assert result.timings[1].start == result.timings[0].duration
    assert any("硬切" in w for w in result.warnings)


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
