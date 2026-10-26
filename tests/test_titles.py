"""标题：常驻大标题、片头标题卡、镜头小标题、AI 生成标题。"""

import json

from shotwright.core.models import Project, Shot, ShotVisual, TitleSettings, VoiceSettings
from shotwright.media import subtitles as subs
from shotwright.pipeline.render import Renderer, shot_title_margin


def test_title_layers_build_headline_intro_and_shot_titles():
    titles = TitleSettings()
    titles.headline.enabled = True
    titles.headline.text = "尖沙咀杯开赛"
    titles.headline.background_opacity = 0.6
    titles.intro.enabled = True
    titles.intro.subtitle = "第八届峦山谷联赛"
    styles, events = subs.title_layers(
        titles,
        width=1080,
        height=1920,
        family="Noto Sans SC",
        headline_margin=160,
        shot_margin=330,
        margin_l=60,
        margin_r=60,
        shot_titles=[("开场", 0.0, 2.0), ("冠军之争", 2.0, 6.0), ("", 6.0, 8.0)],
    )
    names = [line.split(",")[0] for line in styles]
    assert names == ["Style: Headline", "Style: ShotTitle", "Style: Intro", "Style: IntroSub"]
    assert ",3,14," in styles[0]  # 有底色时用色块
    headline = next(e for e in events if ",Headline," in e)
    assert headline.startswith("Dialogue: 3,0:00:02.50,")  # 片头结束后才出现
    shot_events = [e for e in events if ",ShotTitle," in e]
    assert len(shot_events) == 1 and "冠军之争" in shot_events[0]  # 片头期间的小标题让开
    assert any("\\p1" in e for e in events) and any("尖沙咀杯开赛" in e and ",Intro," in e for e in events)


def test_shot_titles_sit_below_headline():
    project = Project()
    project.titles.headline.enabled = True
    project.titles.headline.text = "第一行\n第二行"
    assert shot_title_margin(project) >= 160 + 76 * 2
    project.titles.headline.enabled = False
    assert shot_title_margin(project) == 330


def test_render_with_titles(store, settings):
    shots = [
        Shot(text="第一句话。", title="开场", visual=ShotVisual(mode="color")),
        Shot(text="第二句话。", visual=ShotVisual(mode="color")),
    ]
    project = store.create(
        Project(title="联赛", voice=VoiceSettings(provider="silent"), shots=shots, loudness=None)
    )
    project.subtitle.enabled = False
    project.titles.headline.enabled = True
    project.titles.headline.text = "尖沙咀杯开赛"
    project.titles.intro.enabled = True
    store.save(project)
    result = Renderer(store, settings).render(project.id)
    assert (store.render_dir(project.id, result.render_id) / result.video).is_file()
    project = store.load(project.id)
    project.titles.headline.text = "换个标题"
    store.save(project)
    again = Renderer(store, settings).render(project.id)
    assert {s.key for s in again.stages if not s.reused} == {"final"}


def test_suggest_titles_api(client, settings):
    from shotwright.providers import registry
    from shotwright.providers.base import ProviderSpec

    class TitleLLM:
        def __init__(self, _settings):
            pass

        def complete(self, messages, *, json_mode=False):
            return json.dumps(
                {
                    "headlines": ["尖沙咀杯开赛", "峦山谷的篮球梦"],
                    "intro_subtitle": "第八届联赛",
                    "shots": [{"index": 1, "title": "开场"}, {"index": 2, "title": "冠军之争"}],
                },
                ensure_ascii=False,
            )

    registry.register(ProviderSpec(kind="llm", id="titler", name="T", offline=True), TitleLLM)
    settings.defaults["llm"] = "titler"
    created = client.post(
        "/api/v2/projects",
        json={
            "mode": "split",
            "script": "第一句话说得长一些，让它单独成为一个镜头好不好呢。第二句也要足够长才行，单独成为一个镜头。",
            "voice": {"provider": "silent"},
        },
    ).json()
    shot_id = created["shots"][0]["id"]
    client.patch(f"/api/v2/projects/{created['id']}/shots/{shot_id}", json={"title": "已有的"})
    result = client.post(
        f"/api/v2/projects/{created['id']}/titles/suggest", json={"apply_headline": True}
    ).json()
    assert result["titles"]["candidates"] == ["尖沙咀杯开赛", "峦山谷的篮球梦"]
    assert result["titles"]["headline"] == {
        **result["titles"]["headline"],
        "enabled": True,
        "text": "尖沙咀杯开赛",
    }
    assert result["titles"]["intro"]["subtitle"] == "第八届联赛"
    assert [s["title"] for s in result["shots"]][:2] == ["已有的", "冠军之争"]  # 已有的不覆盖


def test_subtitle_margin_is_automatic_by_aspect():
    from shotwright.core.models import SubtitleStyle
    from shotwright.pipeline.render import subtitle_margin

    assert SubtitleStyle.model_validate({"margin": 240}).margin is None  # 旧版本的固定默认值改为自动
    assert SubtitleStyle.model_validate({"margin": 300}).margin == 300
    landscape = Project(canvas={"aspect": "16:9"})
    portrait = Project(canvas={"aspect": "9:16"})
    assert subtitle_margin(landscape) == round(1080 * 0.06) + 24  # 1080 × 7% 比通用安全区还小，以安全区为准
    assert subtitle_margin(portrait) == 173  # 1920 × 9%
    portrait.delivery = "douyin"
    assert subtitle_margin(portrait) == round(1920 * 0.20) + 24  # 平台安全区更大时以安全区为准
    landscape.subtitle.margin = 150
    assert subtitle_margin(landscape) == 150  # 手动设置的距离
    landscape.subtitle.margin = 20
    assert subtitle_margin(landscape) == round(1080 * 0.06) + 24  # 手动设置也不会低于安全区


def test_newlines_in_script_do_not_make_three_line_subtitles():
    from pathlib import Path

    font = Path(__file__).resolve().parents[1] / "resources" / "fonts" / "NotoSansSC-Bold.ttf"
    text = "再为自己年轻一次——第九届峦山谷尖莎咀杯，即将开幕\n关于篮球，一半是梦想，一半是生活！"
    for cue in subs.split_cues(text, 0, 10, font_path=font, size=64, max_width=1650):
        assert len(cue.text.split("\n")) <= 2
