"""视频质量专项：声音、诉求、分镜等改动的测试。"""

from test_api import _create

from shotwright.core.models import Project, Shot, ShotVisual, VoiceSettings
from shotwright.media.ffmpeg import measure_loudness, run_ffmpeg
from shotwright.pipeline.render import Renderer, audio_filter


def _sine(path, *, seconds=4.0, volume=1.0):
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-af",
            f"volume={volume}",
            "-c:a",
            "aac",
            str(path),
        ]
    )
    return path


def _project_with_bgm(store, tmp_path, **kwargs):
    shots = [
        Shot(text="第一个镜头。", visual=ShotVisual(mode="color")),
        Shot(text="第二个镜头。", visual=ShotVisual(mode="color")),
    ]
    project = store.create(Project(voice=VoiceSettings(provider="silent"), shots=shots, **kwargs))
    bgm = store.add_asset(project, _sine(tmp_path / "bgm.m4a", volume=0.05), kind="audio", source="upload")
    project.bgm.asset_id = bgm.id
    project.bgm.volume = 1.0
    store.save(project)
    return project


def test_export_normalizes_loudness(store, settings, tmp_path):
    project = _project_with_bgm(store, tmp_path)
    result = Renderer(store, settings).render(project.id)
    loudness = measure_loudness(store.render_dir(project.id, result.render_id) / result.video)
    assert loudness is not None and abs(loudness - (-14.0)) <= 1.0


def test_loudness_can_be_turned_off(store, settings, tmp_path):
    project = _project_with_bgm(store, tmp_path, loudness=None)
    result = Renderer(store, settings).render(project.id)
    loudness = measure_loudness(store.render_dir(project.id, result.render_id) / result.video)
    # 0.05 倍的正弦波远低于 -14 LUFS，不处理时保持原样。
    assert loudness is not None and loudness < -25


def test_audio_filter_ducks_and_fades_bgm():
    project = Project()
    graph = audio_filter(project, 1, 10.0)
    assert "sidechaincompress" in graph
    assert "afade=t=in:st=0:d=1.00" in graph
    assert "afade=t=out:st=8.000:d=2.00" in graph
    assert graph.endswith("[a]")

    project.bgm.duck = False
    project.bgm.fade_in = 0
    project.loudness = None
    graph = audio_filter(project, 1, 10.0)
    assert "sidechaincompress" not in graph and "afade=t=in" not in graph
    assert graph.endswith("anull[a]")


def test_audio_filter_without_bgm_is_empty():
    assert audio_filter(Project(), None, 5.0) == ""


def test_ducking_lowers_music_under_voice(store, settings, tmp_path):
    """有声音的主轨（模拟配音）会把背景音乐压下去。"""
    voice = _sine(tmp_path / "voice.m4a", seconds=4.0, volume=0.5)
    music = _sine(tmp_path / "music.m4a", seconds=4.0, volume=0.5)
    project = Project()
    project.bgm.volume = 1.0
    project.bgm.fade_in = 0
    project.bgm.fade_out = 0

    def mix(duck):
        project.bgm.duck = duck
        out = tmp_path / f"mix-{duck}.m4a"
        run_ffmpeg(
            [
                "-i",
                str(voice),
                "-i",
                str(music),
                "-filter_complex",
                audio_filter(project, 1, 4.0),
                "-map",
                "[a]",
                "-c:a",
                "aac",
                str(out),
            ]
        )
        return measure_loudness(out)

    assert mix(True) < mix(False) - 1.0


def test_api_turns_loudness_off_and_on(client):
    project = _create(client)
    assert project["loudness"] == -14.0
    patched = client.patch(f"/api/v2/projects/{project['id']}", json={"loudness": None}).json()
    assert patched["loudness"] is None
    patched = client.patch(f"/api/v2/projects/{project['id']}", json={"title": "x"}).json()
    assert patched["loudness"] is None  # 不传就不改
    patched = client.patch(f"/api/v2/projects/{project['id']}", json={"loudness": -16}).json()
    assert patched["loudness"] == -16


class _RecordingLLM:
    def __init__(self, reply):
        self.reply = reply
        self.messages = []

    def complete(self, messages, *, json_mode=False):
        self.messages.append(messages)
        return self.reply


def test_video_type_is_stored_and_sent_to_llm():
    from shotwright.services import storyboard

    llm = _RecordingLLM('{"title": "t", "shots": [{"text": "一句话。", "terms": ["a"], "prompt": "p"}]}')
    storyboard.generate_storyboard(llm, "卖咖啡机", video_type="product_ad")
    assert "视频类型：商品广告" in llm.messages[0][-1]["content"]


def test_api_saves_video_type_and_footage_sets_default(client, tmp_path):
    from conftest import make_clip

    created = client.post(
        "/api/v2/projects",
        json={"mode": "split", "script": "一句话。", "video_type": "brand", "voice": {"provider": "silent"}},
    ).json()
    assert created["video_type"] == "brand"
    assert client.patch(f"/api/v2/projects/{created['id']}", json={"video_type": "talking"}).json()[
        "video_type"
    ] == "talking"
    assert client.patch(f"/api/v2/projects/{created['id']}", json={"video_type": "nope"}).status_code == 422

    empty = client.post("/api/v2/projects", json={"mode": "empty"}).json()
    clip = make_clip(tmp_path / "raw.mp4", seconds=1.0)
    with clip.open("rb") as handle:
        response = client.post(f"/api/v2/projects/{empty['id']}/footage", files={"file": ("raw.mp4", handle)})
    assert response.status_code == 201, response.text
    assert client.get(f"/api/v2/projects/{empty['id']}").json()["video_type"] == "footage"
