import pytest
from conftest import FakeStock, make_clip

from shotwright.core.models import Project, Shot, ShotVisual, VoiceSettings
from shotwright.media.ffmpeg import probe
from shotwright.pipeline.render import Renderer, RenderError


def _project(store, **kwargs):
    shots = [
        Shot(text="第一个镜头，介绍主题。", visual=ShotVisual(mode="color", color="#AA0000")),
        Shot(text="第二个镜头讲细节。", visual=ShotVisual(mode="color", color="#00AA00")),
        Shot(text="第三个镜头收尾。", visual=ShotVisual(mode="color", color="#0000AA")),
    ]
    return store.create(Project(voice=VoiceSettings(provider="silent"), shots=shots, **kwargs))


def _executed(result):
    return {s.key for s in result.stages if not s.reused}


def test_render_produces_video_with_audio_and_subtitles(store, settings):
    project = _project(store)
    result = Renderer(store, settings).render(project.id)
    out = store.render_dir(project.id, result.render_id)
    info = probe(out / result.video)
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (1080, 1920)
    total = sum(t.duration for t in result.timings)
    assert abs(info.duration - total) < 0.3
    srt = (out / "subtitles.srt").read_text(encoding="utf-8")
    assert "第一个镜头" in srt and "收尾" in srt


def test_changing_one_shot_only_rerenders_that_shot(store, settings):
    project = _project(store)
    Renderer(store, settings).render(project.id)

    project = store.load(project.id)
    second = project.shots[1].id
    project.shots[1].text = "第二个镜头的文案改过了。"
    store.save(project)

    result = Renderer(store, settings).render(project.id)
    assert _executed(result) == {f"{second}:voice", f"{second}:shot", "assembly", "final"}


def test_subtitle_style_change_reuses_all_shots(store, settings):
    project = _project(store)
    Renderer(store, settings).render(project.id)
    project = store.load(project.id)
    project.subtitle.size = 80
    store.save(project)
    result = Renderer(store, settings).render(project.id)
    assert _executed(result) == {"final"}


def test_rerender_without_changes_reuses_everything(store, settings):
    project = _project(store)
    Renderer(store, settings).render(project.id)
    result = Renderer(store, settings).render(project.id)
    assert _executed(result) == set()


def test_uploaded_clip_and_image_and_bgm(store, settings, tmp_path):
    project = _project(store, canvas={"aspect": "16:9"})
    clip = make_clip(tmp_path / "clip.mp4", seconds=1.0, size="640x360")
    image = tmp_path / "still.png"
    from shotwright.media.ffmpeg import run_ffmpeg

    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=400x400", "-frames:v", "1", str(image)])
    bgm = tmp_path / "bgm.m4a"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "aac", str(bgm)])

    a = store.add_asset(project, clip, kind="video", source="upload")
    b = store.add_asset(project, image, kind="image", source="upload")
    m = store.add_asset(project, bgm, kind="audio", source="upload")
    project.shots[0].visual = ShotVisual(mode="upload", selected=a.id, candidates=[a.id])
    project.shots[1].visual = ShotVisual(mode="upload", selected=b.id, candidates=[b.id], fit="contain")
    project.bgm.asset_id = m.id
    store.save(project)

    result = Renderer(store, settings).render(project.id)
    info = probe(store.render_dir(project.id, result.render_id) / "video.mp4")
    assert (info.width, info.height) == (1920, 1080)
    # 1 秒的素材被循环到镜头时长，背景音乐被循环到整片时长。
    assert info.duration > 4


def test_auto_fetch_fills_missing_visuals_and_skips_locked(store, settings):
    settings.auto_fetch_visuals = True
    FakeStock.calls.clear()
    shots = [
        Shot(text="城市夜景。", visual=ShotVisual(mode="stock", terms=["city night"])),
        Shot(text="锁定的镜头。", visual=ShotVisual(mode="stock", terms=["locked"], locked=True)),
    ]
    project = store.create(Project(voice=VoiceSettings(provider="silent"), shots=shots))
    result = Renderer(store, settings).render(project.id)
    assert FakeStock.calls == ["city night"]
    saved = store.load(project.id)
    assert saved.shots[0].visual.selected in saved.assets
    assert saved.shots[1].visual.selected is None
    assert not result.warnings


def test_missing_provider_config_becomes_a_warning(store, settings):
    settings.auto_fetch_visuals = True
    settings.defaults["stock"] = "pexels"  # 没有配置 API Key
    project = store.create(
        Project(
            voice=VoiceSettings(provider="silent"),
            shots=[Shot(text="hi", visual=ShotVisual(mode="stock", terms=["x"]))],
        )
    )
    result = Renderer(store, settings).render(project.id)
    assert result.warnings and "纯色画面" in result.warnings[0]


def test_empty_project_is_an_error(store, settings):
    project = store.create(Project())
    with pytest.raises(RenderError):
        Renderer(store, settings).render(project.id)


def test_unknown_tts_provider_fails_with_shot_number(store, settings):
    project = store.create(Project(voice=VoiceSettings(provider="nope"), shots=[Shot(text="你好")]))
    with pytest.raises(RenderError, match="镜头 1"):
        Renderer(store, settings).render(project.id)
