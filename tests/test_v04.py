"""v0.4 新功能的回归测试：费用估算、转场时间轴、改画面、Host 校验、原片导入、任务恢复、元数据。"""

import subprocess

import pytest
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
from shotwright.providers.base import ProviderError, ProviderSpec
from shotwright.services import cost, visuals

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


# ---- 费用估算 ---------------------------------------------------------------


def test_cost_estimate_for_ai_shot_without_visual(store, settings):
    settings.providers["video_gen"] = {"seedance": {"min_duration": "4", "max_duration": "12"}}
    project = _project(
        store,
        shots=[
            Shot(text="短句。", visual=ShotVisual(mode="ai", prompt="a cat", provider="seedance")),
            Shot(text="长" * 400, visual=ShotVisual(mode="ai", prompt="a dog", provider="seedance")),
            Shot(text="有时长", duration=6.2, visual=ShotVisual(mode="ai", prompt="x", provider="seedance")),
        ],
    )
    estimate = cost.estimate_render_cost(project, settings)
    seconds = [item.visual_cost / 0.14 for item in estimate.items]
    # 短镜头按最短 4 秒计费，长镜头封顶在配置的 12 秒，手动时长向上取整到 7 秒。
    assert [round(s) for s in seconds] == [4, 12, 7]
    assert estimate.total > 0


def test_cost_estimate_endpoint(settings, store):
    project = _project(store, shots=[Shot(text="一句话。", visual=ShotVisual(mode="ai", prompt="sea"))])
    with _client(settings, store) as client:
        r = client.get(f"/api/v2/projects/{project.id}/cost-estimate")
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["shot_id"] == project.shots[0].id


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


# ---- 改画面（视频转视频） ----------------------------------------------------


def _ai_shot_with_video(store, settings, provider):
    project = _project(store, shots=[Shot(text="一句话。", visual=ShotVisual(mode="ai", prompt="sea", provider=provider))])
    shot = project.shots[0]
    visuals.generate_ai_candidate(store, settings, project, shot, confirm_charge=True)
    store.save(project)
    return project, shot


def test_plain_generate_never_sends_reference_video(store, settings):
    project, shot = _ai_shot_with_video(store, settings, "fakeedit")
    visuals.generate_ai_candidate(store, settings, project, shot, confirm_charge=True)
    assert FakeVideoGen.calls[-1]["reference_video"] is None


def test_edit_selected_sends_reference_video(store, settings):
    project, shot = _ai_shot_with_video(store, settings, "fakeedit")
    selected = shot.visual.selected
    visuals.generate_ai_candidate(store, settings, project, shot, confirm_charge=True, edit_selected=True)
    call = FakeVideoGen.calls[-1]
    assert call["first_frame"] is None
    assert call["reference_video"] == store.asset_path(project, selected)


def test_edit_selected_requires_capable_provider(store, settings):
    project, shot = _ai_shot_with_video(store, settings, "fakevid")
    before = len(FakeVideoGen.calls)
    try:
        visuals.generate_ai_candidate(store, settings, project, shot, confirm_charge=True, edit_selected=True)
    except ProviderError as exc:
        assert "不支持改画面" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当拒绝")
    assert len(FakeVideoGen.calls) == before


def test_edit_endpoint_switches_stock_shot_to_ai(settings, store, tmp_path):
    clip = make_clip(tmp_path / "clip.mp4")
    project = _project(store, shots=[Shot(text="一句话。", visual=ShotVisual(mode="upload", prompt="sea", provider="fakeedit"))])
    asset = visuals.add_upload(store, project, clip, filename="clip.mp4")
    project.shots[0].visual.selected = asset.id
    project.shots[0].visual.candidates = [asset.id]
    store.save(project)
    with _client(settings, store) as client:
        url = f"/api/v2/projects/{project.id}/shots/{project.shots[0].id}/candidates"
        assert client.post(url, json={"edit_selected": True}).status_code == 402
        r = client.post(url, json={"edit_selected": True, "confirm_charge": True})
    assert r.status_code == 200, r.text
    visual = r.json()["shots"][0]["visual"]
    assert visual["mode"] == "ai" and visual["selected"] != asset.id and asset.id in visual["candidates"]


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


# ---- 原片导入 ---------------------------------------------------------------


def test_footage_import_splits_into_shots(settings, store, tmp_path):
    first = make_clip(tmp_path / "a.mp4", seconds=2.0, pattern="testsrc2")
    second = make_clip(tmp_path / "b.mp4", seconds=2.0, pattern="smptebars")
    joined = tmp_path / "joined.mp4"
    listing = tmp_path / "list.txt"
    listing.write_text(f"file '{first.as_posix()}'\nfile '{second.as_posix()}'\n", encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)],
        check=True,
    )
    project = _project(store)
    with _client(settings, store) as client:
        with joined.open("rb") as handle:
            r = client.post(
                f"/api/v2/projects/{project.id}/footage?replace_shots=true",
                files={"file": ("joined.mp4", handle, "video/mp4")},
            )
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["shots"]) >= 2
    saved = store.load(project.id)
    assert [s.id for s in saved.shots] == body["shots"]


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


def test_paid_generation_adds_to_project_cost(store, settings):
    registry.register(
        ProviderSpec(kind="video_gen", id="seedance-fake", name="S", paid=True), FakeVideoGen
    )
    cost._VIDEO_GEN_PRICE["seedance-fake"] = 0.5
    try:
        project = _project(
            store, shots=[Shot(text="一句话。", visual=ShotVisual(mode="ai", prompt="sea", provider="seedance-fake"))]
        )
        visuals.generate_ai_candidate(store, settings, project, project.shots[0], confirm_charge=True)
        # 按最短 4 秒计费：0.5 × 4 = 2 元，直接写进项目文件。
        assert store.load(project.id).cost_credits == pytest.approx(2.0)
        assert project.cost_credits == pytest.approx(2.0)
    finally:
        del cost._VIDEO_GEN_PRICE["seedance-fake"]
