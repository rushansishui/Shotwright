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


def test_sample_render_never_spends(store, settings):
    """整片样片：就算调用方说要收费，也一律按免费草稿档跑。"""
    from shotwright.api.jobs import JobManager

    project = _project(store)
    jobs = JobManager(store, settings)
    job = jobs.submit(project.id, allow_paid=True, quality="final", sample=True)
    assert job.sample is True
    assert job.allow_paid is False
    assert job.quality == "draft"


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


def test_missing_provider_config_becomes_a_warning(store, settings, monkeypatch):
    from shotwright.services import visuals

    settings.auto_fetch_visuals = True
    settings.defaults["stock"] = "pexels"  # 没有配置 API Key
    monkeypatch.setattr(visuals, "FREE_STOCK", "pexels")  # 不改用免 Key 的素材库，测试离线
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


def test_ai_shot_falls_back_to_stock_when_ai_unavailable(store, settings):
    settings.auto_fetch_visuals = True
    shot = Shot(text="城市夜景。", visual=ShotVisual(mode="ai", prompt="city", terms=["city night"]))
    project = store.create(Project(voice=VoiceSettings(provider="silent"), shots=[shot]))
    result = Renderer(store, settings).render(project.id)
    saved = store.load(project.id)
    assert saved.shots[0].visual.selected in saved.assets
    assert "已改用素材检索" in result.warnings[0]


def test_paid_ai_shot_needs_confirmation(store, settings):
    from conftest import FakeVideoGen

    settings.auto_fetch_visuals = True
    shot = Shot(text="海边日落。", visual=ShotVisual(mode="ai", prompt="sunset", terms=["sunset"]))
    project = store.create(Project(voice=VoiceSettings(provider="silent"), shots=[shot]))
    result = Renderer(store, settings).render(project.id)
    assert FakeVideoGen.calls == []
    assert "需要确认" in result.warnings[0]

    project = store.load(project.id)
    project.shots[0].visual.selected = None
    project.shots[0].visual.candidates = []
    store.save(project)
    result = Renderer(store, settings, allow_paid=True).render(project.id)
    assert len(FakeVideoGen.calls) == 1
    assert result.warnings == []
    saved = store.load(project.id)
    assert saved.assets[saved.shots[0].visual.selected].source == "fakevid"


def test_image_shot_gets_ken_burns_motion(store, settings, tmp_path):
    from conftest import make_image

    project = store.create(Project(voice=VoiceSettings(provider="silent")))
    image = store.add_asset(project, make_image(tmp_path / "still.png"), kind="image", source="upload")
    project.shots = [Shot(text="一张图片慢慢推近。", visual=ShotVisual(mode="image", selected=image.id))]
    store.save(project)
    result = Renderer(store, settings).render(project.id)
    video = store.render_dir(project.id, result.render_id) / result.video
    info = probe(video)
    assert info.width == 1080 and info.height == 1920
    assert abs(info.duration - result.duration) < 0.2

    # 首帧和末帧应该不同（有运动），用两帧的 MD5 比较。
    from shotwright.media.ffmpeg import run_ffmpeg

    frames = []
    for at in ("0", f"{result.duration - 0.1:.2f}"):
        out = tmp_path / f"f{at}.png"
        run_ffmpeg(["-ss", at, "-i", str(video), "-frames:v", "1", str(out)])
        frames.append(out.read_bytes())
    assert frames[0] != frames[1]


def test_color_mode_ignores_selected_asset(store, settings, tmp_path):
    project = store.create(Project(voice=VoiceSettings(provider="silent")))
    clip = store.add_asset(project, make_clip(tmp_path / "c.mp4"), kind="video", source="upload")
    project.shots = [Shot(text="纯色。", visual=ShotVisual(mode="color", color="#FF0000", selected=clip.id))]
    store.save(project)
    result = Renderer(store, settings).render(project.id)
    out = tmp_path / "frame.rgb"
    from shotwright.media.ffmpeg import run_ffmpeg

    run_ffmpeg(
        [
            "-ss",
            "0.2",
            "-i",
            str(store.render_dir(project.id, result.render_id) / result.video),
            "-frames:v",
            "1",
            "-vf",
            "scale=1:1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            str(out),
        ]
    )
    r, g, b = out.read_bytes()[:3]
    assert r > 200 and g < 60 and b < 60  # 纯红，没有用到选中的视频


def test_long_brief_is_not_used_as_stock_query(store, settings):
    """视频诉求可能是整段说明，不能直接当素材检索词。"""
    settings.auto_fetch_visuals = True
    FakeStock.calls.clear()
    long_brief = "请做一条关于城市夜景的片子，" * 20  # 远超 80 字节
    shot = Shot(text="画面。", visual=ShotVisual(mode="stock"))
    project = store.create(Project(subject=long_brief, voice=VoiceSettings(provider="silent"), shots=[shot]))
    result = Renderer(store, settings).render(project.id)
    assert FakeStock.calls == []
    assert result.warnings and "检索词" in result.warnings[0]


def test_short_topic_is_still_a_fallback_query(store, settings):
    settings.auto_fetch_visuals = True
    FakeStock.calls.clear()
    shot = Shot(text="画面。", visual=ShotVisual(mode="stock"))
    project = store.create(
        Project(subject="city night", voice=VoiceSettings(provider="silent"), shots=[shot])
    )
    Renderer(store, settings).render(project.id)
    assert FakeStock.calls == ["city night"]


def test_unconfigured_stock_falls_back_to_free_library(settings):
    """默认素材库没填 Key 时改用免 Key 的 Coverr，不填任何 Key 也能找到画面。"""
    from shotwright.services import visuals

    settings.defaults["stock"] = "pexels"
    assert visuals._provider_id(settings, "stock", Shot(text="hi")) == "coverr"
    settings.providers["stock"] = {"pexels": {"api_key": "k"}}
    assert visuals._provider_id(settings, "stock", Shot(text="hi")) == "pexels"
