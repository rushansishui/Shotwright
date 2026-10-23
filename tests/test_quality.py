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
    assert (
        client.patch(f"/api/v2/projects/{created['id']}", json={"video_type": "talking"}).json()["video_type"]
        == "talking"
    )
    assert client.patch(f"/api/v2/projects/{created['id']}", json={"video_type": "nope"}).status_code == 422

    empty = client.post("/api/v2/projects", json={"mode": "empty"}).json()
    clip = make_clip(tmp_path / "raw.mp4", seconds=1.0)
    with clip.open("rb") as handle:
        response = client.post(f"/api/v2/projects/{empty['id']}/footage", files={"file": ("raw.mp4", handle)})
    assert response.status_code == 201, response.text
    assert client.get(f"/api/v2/projects/{empty['id']}").json()["video_type"] == "footage"


class _ScriptedLLM:
    """按固定回复写分镜的大模型。"""

    def __init__(self, settings=None):
        pass

    def complete(self, messages, *, json_mode=False):
        return (
            '{"title": "t", "shots": ['
            '{"text": "第一个镜头的文案。", "terms": ["coffee"], "prompt": "a white coffee maker on a desk"},'
            '{"text": "第二个镜头的文案。", "terms": ["subway"], "prompt": "a woman runs to the subway"}]}'
        )


def test_bench_runs_briefs_and_reports(store, settings, tmp_path):
    from shotwright.providers import registry
    from shotwright.providers.base import ProviderSpec
    from shotwright.services import bench

    registry.register(ProviderSpec(kind="llm", id="scripted", name="Scripted", offline=True), _ScriptedLLM)
    settings.defaults["llm"] = "scripted"
    settings.auto_fetch_visuals = True
    report = bench.run_bench(
        store,
        settings,
        tmp_path / "run",
        render=True,
        aspects=["16:9"],
        only=["product_ad"],
        progress=lambda m: None,
    )
    case = report.cases[0]
    assert not case.error, case.error
    assert case.shots == 2 and case.prompt_avg == 6.5
    assert case.rendered and case.loudness is None  # 静音配音、没有背景音乐：测不出响度
    # 假素材 2 秒，和镜头时长相当，几乎不需要补足。
    assert case.fill_ratio < 0.05 and case.blank_ratio == 0.0
    text = (tmp_path / "run" / "report.md").read_text(encoding="utf-8")
    assert "画面描述平均长度" in text and "product_ad" in text
    compared = bench.format_report(report.to_dict(), report.to_dict())
    assert "| 变化 |" in compared


def test_prompt_size_counts_words_and_cjk():
    from shotwright.services.bench import prompt_size

    assert prompt_size("a white coffee maker") == 4
    assert prompt_size("白色咖啡机放在木桌上") == 5



def test_compose_prompt_adds_look_camera_link_and_no_text():
    from shotwright.core.models import Look, LookSubject
    from shotwright.services import prompting

    project = Project(
        look=Look(
            subjects=[
                LookSubject(code="P1", name="咖啡机", kind="product", appearance="哑光白色圆柱形机身"),
                LookSubject(code="A", name="女主", kind="person", appearance="黑色短发"),
            ],
            scene="城市公寓",
            style="明亮干净",
            avoid="其他品牌",
        )
    )
    shot = Shot(
        text="x", visual=ShotVisual(prompt="P1 放在桌上", camera="orbit", link="承接上一镜头的手部动作")
    )
    text = prompting.compose(project, shot)
    assert text.startswith("P1 放在桌上")
    assert "运镜：镜头绕主体缓慢环绕" in text and "衔接：承接上一镜头的手部动作" in text
    assert "P1（咖啡机）：哑光白色圆柱形机身" in text and "黑色短发" not in text  # 只带上用到的主体
    assert "场景：城市公寓" in text and "不要出现：其他品牌" in text and "不出现任何文字" in text

    image = prompting.compose(project, shot, target="image")
    assert "运镜" not in image and "静态画面" in image

    project.language = "en-US"
    assert "Camera: Slow orbit" in prompting.compose(project, shot)


def test_prompt_is_translated_when_model_lacks_language(store, settings):
    from shotwright.providers import registry
    from shotwright.providers.base import ProviderSpec
    from shotwright.services import prompting, visuals

    class Translator:
        def __init__(self, _settings):
            pass

        def complete(self, messages, *, json_mode=False):
            return "TRANSLATED: " + messages[-1]["content"][:10]

    registry.register(ProviderSpec(kind="llm", id="translator", name="T", offline=True), Translator)
    settings.defaults["llm"] = "translator"
    project = store.create(
        Project(
            voice=VoiceSettings(provider="silent"),
            shots=[Shot(text="x", visual=ShotVisual(mode="image", prompt="一只猫"))],
        )
    )
    prompting._cache.clear()
    # 假的文生图只声明支持英文：中文项目提交前先翻译。
    assert visuals.build_prompt(settings, project, project.shots[0], "image_gen", "fakeimg").startswith(
        "TRANSLATED"
    )
    # Seedream 支持中文：原样提交。
    assert visuals.build_prompt(settings, project, project.shots[0], "image_gen", "seedream").startswith(
        "一只猫"
    )


def test_api_updates_look_and_shot_camera(client):
    created = _create(client)
    look = {
        "subjects": [{"code": "P1", "name": "咖啡机", "kind": "product", "appearance": "白色"}],
        "scene": "公寓",
        "style": "",
        "avoid": "",
    }
    patched = client.patch(f"/api/v2/projects/{created['id']}", json={"look": look}).json()
    assert patched["look"]["subjects"][0]["code"] == "P1"
    bad = {**look, "subjects": [look["subjects"][0], look["subjects"][0]]}
    assert client.patch(f"/api/v2/projects/{created['id']}", json={"look": bad}).status_code == 422
    shot_id = created["shots"][0]["id"]
    shot = client.patch(
        f"/api/v2/projects/{created['id']}/shots/{shot_id}",
        json={"visual": {"camera": "crane", "link": "接上一镜"}},
    ).json()
    target = next(s for s in shot["shots"] if s["id"] == shot_id) if "shots" in shot else shot
    assert target["visual"]["camera"] == "crane" and target["visual"]["link"] == "接上一镜"
