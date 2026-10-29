"""镜头内剪辑：入出点、素材不够长的补足、取景、原声、拆分与合并。"""

import subprocess

import pytest
from conftest import make_clip

from shotwright.core.models import Project, Shot, ShotVisual, VoiceSettings
from shotwright.media.ffmpeg import probe, run_ffmpeg
from shotwright.pipeline.render import Renderer
from shotwright.pipeline.shots import plan_clip
from shotwright.services import editing, visuals


def _clip_with_audio(path, seconds=4.0):
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=640x360:rate=30:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=600:duration={seconds}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )
    return path


def _project_with_clip(store, tmp_path, *, seconds=4.0, audio=False, **visual):
    clip = (
        _clip_with_audio(tmp_path / "clip.mp4", seconds)
        if audio
        else make_clip(tmp_path / "clip.mp4", seconds=seconds)
    )
    project = store.create(
        Project(voice=VoiceSettings(provider="silent"), shots=[Shot(text="", duration=2.0)])
    )
    asset = visuals.add_upload(store, project, clip, filename="clip.mp4")
    shot = project.shots[0]
    shot.visual = ShotVisual(
        mode="upload", candidates=[asset.id], selected=asset.id, edit_asset=asset.id, **visual
    )
    store.save(project)
    return store.load(project.id), asset


# ---- 片段规划 ---------------------------------------------------------------


def test_plan_clip_slow_then_hold():
    visual = ShotVisual(trim_start=1.0, trim_end=3.0)
    plan = plan_clip(visual, 10.0, 4.0, has_audio=False)
    assert plan.start == 1.0 and plan.length == 2.0
    assert plan.speed == pytest.approx(0.8)
    # 2 秒慢放到 2.5 秒，剩下 1.5 秒定格。
    assert plan.hold == pytest.approx(1.5)
    assert not plan.loop


def test_plan_clip_modes_and_clamping():
    assert plan_clip(ShotVisual(fill="hold"), 2.0, 3.0, False).hold == pytest.approx(1.0)
    assert plan_clip(ShotVisual(fill="loop"), 2.0, 3.0, False).loop
    # 慢放 0.9 倍就够时不定格。
    plan = plan_clip(ShotVisual(), 2.7, 3.0, False)
    assert plan.speed == pytest.approx(0.9) and plan.hold == pytest.approx(0.0)
    # 入出点超出素材时收回到素材范围内。
    plan = plan_clip(ShotVisual(trim_start=5.0, trim_end=50.0), 6.0, 0.5, False)
    assert plan.start == 5.0 and plan.length == pytest.approx(1.0)
    # 原声只在素材有声音且音量大于 0 时启用。
    assert not plan_clip(ShotVisual(source_volume=1.0), 4.0, 2.0, has_audio=False).audio
    assert plan_clip(ShotVisual(source_volume=1.0), 4.0, 2.0, has_audio=True).audio


def test_trim_belongs_to_its_asset():
    visual = ShotVisual(selected="a1", edit_asset="a1", trim_start=2.0, trim_end=3.0, focus_x=0.1)
    assert visual.effective_edit().trim_start == 2.0
    other = visual.model_copy(update={"selected": "a2"})
    edit = other.effective_edit()
    assert (edit.trim_start, edit.trim_end, edit.focus_x) == (0.0, None, 0.5)


def test_trim_end_must_follow_start():
    with pytest.raises(ValueError):
        ShotVisual(trim_start=3.0, trim_end=3.0)


# ---- 渲染 -------------------------------------------------------------------


@pytest.mark.parametrize("fill", ["slow_hold", "hold", "loop"])
def test_short_clip_fills_shot(store, settings, tmp_path, fill):
    project, _ = _project_with_clip(store, tmp_path, seconds=1.0, fill=fill)
    project.shots[0].duration = 2.5
    store.save(project)
    result = Renderer(store, settings).render(project.id)
    info = probe(store.render_dir(project.id, result.render_id) / result.video)
    assert info.duration == pytest.approx(2.5, abs=0.15)


def test_trimmed_range_is_used(store, settings, tmp_path):
    project, _ = _project_with_clip(store, tmp_path, seconds=4.0, trim_start=1.0, trim_end=2.0)
    first = Renderer(store, settings).render(project.id)
    project = store.load(project.id)
    project.shots[0].visual.trim_end = 3.0
    project.shots[0].visual.trim_start = 2.0
    store.save(project)
    second = Renderer(store, settings).render(project.id)
    # 改了入出点，镜头重新渲染（指纹不同），成片时长不变。
    shot_fp = lambda result: next(s.fingerprint for s in result.stages if s.kind == "shot")  # noqa: E731
    assert shot_fp(first) != shot_fp(second)
    info = probe(store.render_dir(project.id, second.render_id) / second.video)
    assert info.duration == pytest.approx(2.0, abs=0.15)


@pytest.mark.parametrize("fit", ["cover", "contain", "blur"])
def test_fit_modes_render(store, settings, tmp_path, fit):
    project, _ = _project_with_clip(store, tmp_path, seconds=2.0, fit=fit, focus_x=0.0)
    result = Renderer(store, settings).render(project.id)
    info = probe(store.render_dir(project.id, result.render_id) / result.video)
    assert (info.width, info.height) == (1080, 1920)


def _mean_volume(path):
    output = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    ).stderr
    line = next(item for item in output.splitlines() if "mean_volume" in item)
    return float(line.split("mean_volume:")[1].split("dB")[0])


def test_source_audio_is_kept_when_enabled(store, settings, tmp_path):
    project, _ = _project_with_clip(store, tmp_path, seconds=2.0, audio=True, source_volume=1.0)
    result = Renderer(store, settings).render(project.id)
    loud = _mean_volume(store.render_dir(project.id, result.render_id) / result.video)

    project = store.load(project.id)
    project.shots[0].visual.source_volume = 0.0
    store.save(project)
    result = Renderer(store, settings).render(project.id)
    silent = _mean_volume(store.render_dir(project.id, result.render_id) / result.video)
    assert loud > -40 and silent < loud - 20


# ---- 拆分与合并 -------------------------------------------------------------


def test_split_and_merge_round_trip(store, tmp_path):
    project, asset = _project_with_clip(store, tmp_path, seconds=4.0, trim_start=0.5, trim_end=3.5)
    shot = project.shots[0]
    shot.text = "第一句。"
    shot.duration = 3.0
    second = editing.split_shot(project, shot.id, 1.0)
    first = project.shots[0]
    assert (first.visual.trim_start, first.visual.trim_end) == (0.5, 1.5)
    assert (second.visual.trim_start, second.visual.trim_end) == (1.5, 3.5)
    assert first.duration == pytest.approx(1.0) and second.duration == pytest.approx(2.0)
    assert first.text == "第一句。" and second.text == ""
    assert second.visual.selected == asset.id

    second.text = "第二句。"
    merged = editing.merge_with_next(project, first.id)
    assert len(project.shots) == 1
    assert (merged.visual.trim_start, merged.visual.trim_end) == (0.5, 3.5)
    assert merged.text == "第一句。第二句。"
    assert merged.duration == pytest.approx(3.0)
    Project.model_validate(project.model_dump())


def test_split_rejects_bad_points(store, tmp_path):
    project, _ = _project_with_clip(store, tmp_path, seconds=2.0)
    with pytest.raises(ValueError):
        editing.split_shot(project, project.shots[0].id, 1.95)
    project.shots[0].visual.mode = "color"
    with pytest.raises(ValueError):
        editing.split_shot(project, project.shots[0].id, 1.0)


def test_merge_last_shot_is_rejected(store, tmp_path):
    project, _ = _project_with_clip(store, tmp_path)
    with pytest.raises(ValueError):
        editing.merge_with_next(project, project.shots[0].id)


def test_merge_different_sources_keeps_first_visual(store, tmp_path):
    project, asset = _project_with_clip(store, tmp_path)
    project.shots.append(Shot(text="Hello", visual=ShotVisual(mode="color")))
    project.shots[0].text = "Hi"
    merged = editing.merge_with_next(project, project.shots[0].id)
    assert merged.visual.selected == asset.id and merged.text == "Hi Hello"
    assert merged.duration is None


# ---- 接口 -------------------------------------------------------------------


def _client(settings, store):
    from fastapi.testclient import TestClient

    from shotwright.api.app import create_app
    from shotwright.api.jobs import JobManager

    return TestClient(create_app(settings, job_manager=JobManager(store, settings)))


def test_api_trim_split_merge(store, settings, tmp_path):
    project, asset = _project_with_clip(store, tmp_path, seconds=4.0)
    project.shots[0].visual.edit_asset = None
    store.save(project)
    base = f"/api/v2/projects/{project.id}/shots/{project.shots[0].id}"
    with _client(settings, store) as client:
        r = client.patch(base, json={"visual": {"trim_end": 3.0, "fill": "hold", "source_volume": 0.5}})
        assert r.status_code == 200, r.text
        visual = r.json()["shots"][0]["visual"]
        assert visual["edit_asset"] == asset.id and visual["trim_end"] == 3.0 and visual["fill"] == "hold"

        r = client.patch(base, json={"visual": {"trim_start": 3.5}})
        assert r.status_code == 422
        r = client.patch(base, json={"visual": {"trim_start": 3.5}}, headers={"accept-language": "en"})
        assert r.json()["detail"] == "The out point must be after the in point"

        r = client.post(f"{base}/split", json={"at": 1.0})
        assert r.status_code == 200, r.text
        shots = r.json()["shots"]
        assert len(shots) == 2 and shots[1]["visual"]["trim_start"] == 1.0

        r = client.post(f"{base}/merge")
        assert r.status_code == 200, r.text
        shots = r.json()["shots"]
        assert len(shots) == 1 and shots[0]["visual"]["trim_end"] == 3.0

        assert client.post(f"{base}/merge").status_code == 422
        r = client.patch(base, json={"visual": {"clear_trim_end": True}})
        assert r.json()["shots"][0]["visual"]["trim_end"] is None
