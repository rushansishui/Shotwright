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


_GOOD_PROMPT_ZH = (
    "P1 放在浅色木桌中央，清晨的阳光从左侧窗户斜照进来。一只手伸进画面按下 P1 正面的银色圆形按键，"
    "指示灯亮起，几秒后深褐色的浓缩咖啡缓缓流进白色陶瓷杯，杯口升起细细的热气。近景，主体位于画面中下方，"
    "背景是虚化的书架和绿植，暖色自然光，桌面上有一本摊开的笔记本和一支黑色钢笔，整体干净明亮。"
)


def _board_reply(prompt=_GOOD_PROMPT_ZH, link="开场：从桌面空镜切入"):
    import json

    return json.dumps(
        {
            "title": "一键咖啡",
            "look": {
                "subjects": [
                    {
                        "code": "P1",
                        "name": "便携咖啡机",
                        "kind": "product",
                        "appearance": "哑光白色圆柱形机身",
                    },
                    {"code": "P1", "name": "重复", "kind": "product", "appearance": "x"},
                    {"code": "1bad", "name": "非法代号"},
                ],
                "scene": "城市公寓",
                "style": "明亮干净",
                "avoid": "其他品牌",
            },
            "shots": [
                {
                    "text": "早上没时间？",
                    "terms": ["coffee"],
                    "prompt": prompt,
                    "camera": "push_in",
                    "link": link,
                },
                {"text": "一键出咖啡。", "terms": ["cup"], "prompt": prompt, "camera": "zoom", "link": link},
            ],
        },
        ensure_ascii=False,
    )


def test_write_storyboard_parses_look_camera_and_link():
    from shotwright.services import storyboard

    llm = _RecordingLLM(_board_reply())
    board = storyboard.write_storyboard(llm, "卖咖啡机", video_type="product_ad", aspect="9:16", duration=8)
    assert [s.code for s in board.look.subjects] == ["P1"]
    assert board.look.subjects[0].appearance == "哑光白色圆柱形机身"
    assert board.shots[0].visual.camera == "push_in" and board.shots[1].visual.camera == "auto"
    assert board.shots[0].visual.link.startswith("开场")
    assert board.issues == [] and len(llm.messages) == 1
    user = llm.messages[0][-1]["content"]
    assert "画面比例：竖屏 9:16" in user and "目标时长：约 8 秒" in user and "诉求原文" in user
    assert "120–200 字" in llm.messages[0][0]["content"]


def test_write_storyboard_rewrites_once_when_check_fails():
    from shotwright.services import storyboard

    class TwoReplies:
        def __init__(self):
            self.calls = []

        def complete(self, messages, *, json_mode=False):
            self.calls.append(messages)
            return _board_reply(prompt="P2 很短", link="") if len(self.calls) == 1 else _board_reply()

    llm = TwoReplies()
    board = storyboard.write_storyboard(llm, "卖咖啡机", video_type="product_ad")
    assert len(llm.calls) == 2
    feedback = llm.calls[1][-1]["content"]
    assert "画面描述只有" in feedback and "缺少衔接说明" in feedback and "P2" in feedback
    assert board.issues == [] and board.shots[0].visual.prompt == _GOOD_PROMPT_ZH


def test_check_storyboard_flags_duration_and_missing_subjects():
    from shotwright.core.models import Look
    from shotwright.services.storyboard import Storyboard, check_storyboard

    board = Storyboard(
        title="t", shots=[Shot(text="很短。", visual=ShotVisual(prompt="x", link="y"))], look=Look()
    )
    issues = check_storyboard(board, lang="en", duration=60, video_type="product_ad")
    assert any("目标约 60 秒" in issue for issue in issues)
    assert any("缺少主体" in issue for issue in issues)
    assert any("个词" in issue for issue in issues)


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


class _VaryingVideo:
    """每次生成不同画面的假 AI 视频服务（素材按内容去重，相同画面会被当成同一个素材）。"""

    patterns = ["testsrc2", "smptebars", "rgbtestsrc", "yuvtestsrc", "pal75bars", "smptehdbars"]
    calls: list[dict] = []

    def __init__(self, settings):
        self.resolution = settings.get("resolution") or "1080p"

    def generate(
        self, prompt, *, aspect, duration, output, first_frame=None, reference_video=None, on_task=None
    ):
        from conftest import make_clip

        _VaryingVideo.calls.append(
            {
                "prompt": prompt,
                "duration": duration,
                "first_frame": first_frame,
                "resolution": self.resolution,
            }
        )
        pattern = self.patterns[len(_VaryingVideo.calls) % len(self.patterns)]
        return make_clip(output.with_suffix(".mp4"), seconds=min(duration, 3.0), pattern=pattern)


def _varying(settings, *, max_duration=None):
    from shotwright.providers import registry
    from shotwright.providers.base import ProviderSpec

    registry.register(
        ProviderSpec(kind="video_gen", id="varying", name="Varying", paid=True, max_duration=max_duration),
        _VaryingVideo,
    )
    settings.defaults["video_gen"] = "varying"
    _VaryingVideo.calls.clear()


def test_generation_records_takes_and_prunes_with_candidates(store, settings):
    from shotwright.services import visuals

    _varying(settings)
    shot = Shot(text="一句话。", visual=ShotVisual(mode="ai", prompt="a cat"))
    project = store.create(Project(voice=VoiceSettings(provider="silent"), shots=[shot]))
    shot = project.shots[0]
    first = visuals.refresh_candidates(store, settings, project, shot, confirm_charge=True)[0]
    second = visuals.refresh_candidates(store, settings, project, shot, confirm_charge=True)[0]
    assert [t.asset_id for t in shot.visual.takes] == [first, second]
    assert shot.visual.take(second).kind == "video" and shot.visual.take(second).prompt == "a cat"
    assert shot.visual.takes[0].batch != shot.visual.takes[1].batch

    # 候选被挤出时版本记录一起清理。
    visuals._attach(shot, [], select=False)
    shot.visual.candidates = [second]
    visuals._attach(shot, [])
    assert [t.asset_id for t in shot.visual.takes] == [second]


def test_image_prompt_is_used_for_images_and_falls_back():
    from shotwright.services import prompting

    project = Project()
    shot = Shot(text="x", visual=ShotVisual(prompt="P1 被拿起后放下", image_prompt="P1 静置在木桌上"))
    assert prompting.compose(project, shot, target="image").startswith("P1 静置在木桌上")
    assert prompting.compose(project, shot).startswith("P1 被拿起后放下")
    shot.visual.image_prompt = ""
    assert prompting.compose(project, shot, target="image").startswith("P1 被拿起后放下")


def test_storyboard_reads_still_prompt():
    import json

    from shotwright.services import storyboard

    data = json.loads(_board_reply())
    data["shots"][0]["still"] = "P1 静置在木桌上"
    board = storyboard.write_storyboard(_RecordingLLM(json.dumps(data, ensure_ascii=False)), "x")
    assert board.shots[0].visual.image_prompt == "P1 静置在木桌上"


class _VaryingImage:
    calls: list[str] = []

    def __init__(self, settings):
        pass

    def generate(self, prompt, *, aspect, output):
        from conftest import make_image

        _VaryingImage.calls.append(prompt)
        size = ["320x240", "400x300", "480x360", "560x420"][len(_VaryingImage.calls) % 4]
        return make_image(output.with_suffix(".png"), size=size)


def test_count_keyframes_and_draft_resolution(store, settings):
    from shotwright.providers import registry
    from shotwright.providers.base import ProviderSpec
    from shotwright.services import visuals

    _varying(settings)
    registry.register(ProviderSpec(kind="image_gen", id="varyimg", name="VI", paid=True), _VaryingImage)
    settings.defaults["image_gen"] = "varyimg"
    shot = Shot(text="一句话。", visual=ShotVisual(mode="ai", prompt="P1 被拿起", image_prompt="P1 静置"))
    project = store.create(Project(voice=VoiceSettings(provider="silent"), shots=[shot]))
    shot = project.shots[0]

    frames = visuals.refresh_candidates(store, settings, project, shot, confirm_charge=True, keyframes=2)
    assert len(frames) == 2 and shot.visual.selected is None  # 关键帧不改变选中的画面
    assert {t.kind for t in shot.visual.takes} == {"keyframe"} and len(
        {t.batch for t in shot.visual.takes}
    ) == 1
    assert all(prompt.startswith("P1 静置") for prompt in _VaryingImage.calls)

    videos = visuals.refresh_candidates(
        store, settings, project, shot, confirm_charge=True, count=2, quality="draft"
    )
    assert len(videos) == 2 and shot.visual.selected == videos[-1]
    batches = {t.batch for t in shot.visual.takes if t.kind == "video"}
    assert len(batches) == 1
    assert [call["resolution"] for call in _VaryingVideo.calls] == ["720p", "720p"]
    assert project.assets[videos[0]].meta["resolution"] == "720p"


def test_draft_resolution_halves_video_cost(settings):
    from shotwright.services import cost

    settings.providers.setdefault("video_gen", {})["seedance"] = {"api_key": "k", "resolution": "1080p"}
    project = Project(
        shots=[Shot(text="一句话说完。", visual=ShotVisual(mode="ai", prompt="x", provider="seedance"))]
    )
    final = cost.estimate_render_cost(project, settings).total_visual
    draft = cost.estimate_render_cost(project, settings, "draft").total_visual
    assert final > 0 and abs(draft - final / 2) < 1e-6
