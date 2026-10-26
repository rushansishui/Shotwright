"""字幕和标题的进阶文字效果：阴影方向与模糊、第二层描边、行间距、出场 / 循环效果、关键词高亮。"""

import json

from shotwright.core.models import HeadlineStyle, SubtitleStyle, TitleSettings
from shotwright.media import subtitles as subs


def _events(style: SubtitleStyle, text: str, start: float = 0.0, end: float = 4.0) -> list[str]:
    ass = subs.to_ass([subs.Cue(start, end, text)], style, width=1080, height=1920, family="Noto Sans SC")
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def test_plain_subtitle_is_one_event_on_the_text_layer():
    (event,) = _events(SubtitleStyle(margin=200), "你好世界")
    assert event.startswith("Dialogue: 3,")
    assert event.endswith(",,你好世界")


def test_shadow_angle_and_blur_use_a_separate_shadow_layer():
    style = SubtitleStyle(margin=200, shadow=6, shadow_angle=90, shadow_blur=5)
    shadow, text = _events(style, "你好")
    assert shadow.startswith("Dialogue: 0,") and "\\1a&HFF&" in shadow and "\\blur5" in shadow
    assert "\\xshad0.0\\yshad8.5" in shadow  # 90° 正下方，距离按 45° 时的 (6, 6) 换算
    assert text.startswith("Dialogue: 3,") and text.index("\\shad0") > text.index("\\yshad")


def test_hard_shadow_at_default_angle_keeps_single_event():
    (event,) = _events(SubtitleStyle(margin=200, shadow=4), "你好")
    assert "xshad" not in event


def test_second_outline_draws_a_thicker_outline_layer_underneath():
    style = SubtitleStyle(margin=200, outline=4, outline2=6, outline2_color="#FF3B7F")
    outer, text = _events(style, "你好")
    assert outer.startswith("Dialogue: 1,") and "\\bord10\\3c&H7F3BFF&" in outer
    assert text.startswith("Dialogue: 3,")


def test_second_outline_is_ignored_with_a_box():
    (event,) = _events(SubtitleStyle(margin=200, outline2=6, background_opacity=0.6), "你好")
    assert "bord10" not in event


def test_line_spacing_positions_each_line():
    style = SubtitleStyle(margin=200, size=64, line_spacing=30)
    first, second = _events(style, "第一行\n第二行")
    assert "\\an2\\pos(540,1626)" in first and first.endswith("第一行")
    assert "\\an2\\pos(540,1720)" in second and second.endswith("第二行")
    top = SubtitleStyle(margin=200, size=64, line_spacing=30, position="top")
    first, second = _events(top, "第一行\n第二行")
    assert "\\pos(540,200)" in first and "\\pos(540,294)" in second


def test_line_spacing_keeps_karaoke_timing_across_lines():
    style = SubtitleStyle(margin=200, line_spacing=20, animation="karaoke")
    first, second = _events(style, "一二\n三四", 0.0, 4.0)
    assert first.count("\\kf") == 2 and "\\kf100}一" in first
    assert "{\\kf200}{\\kf100}三" in second  # 第二行先等第一行读完


def test_exit_and_loop_animations():
    (event,) = _events(SubtitleStyle(margin=200, exit="shrink"), "你好", 0.0, 2.0)
    assert "\\t(1740,2000,\\fscx0\\fscy0)" in event
    (event,) = _events(SubtitleStyle(margin=200, exit="fade"), "你好", 0.0, 2.0)
    assert "\\fad(0,300)" in event
    (event,) = _events(SubtitleStyle(margin=200, exit="slide_down"), "你好", 0.0, 2.0)
    assert "\\move(540,1720,540,1752,1700,2000)" in event
    (event,) = _events(SubtitleStyle(margin=200, loop="pulse"), "你好", 0.0, 3.0)
    assert event.count("\\fscx107") == 2  # 0.4 秒后开始，每 1.2 秒一次
    (event,) = _events(SubtitleStyle(margin=200, loop="swing"), "你好", 0.0, 3.0)
    assert "\\frz3" in event and "\\frz-3" in event


def test_headline_loops_until_the_end_of_the_video():
    titles = TitleSettings(headline=HeadlineStyle(enabled=True, text="大标题", loop="pulse", exit="fade"))
    _, events = subs.title_layers(
        titles, width=1080, height=1920, families=dict.fromkeys(("headline", "intro", "shot"), "Noto Sans SC"),
        headline_margin=160, shot_margin=330, margin_l=60, margin_r=60, total=30.0,
    )
    (event,) = events
    assert event.startswith("Dialogue: 33,0:00:00.00,0:00:30.00,")
    assert event.count("\\fscx107") == 24 and "\\fad(0,300)" in event


def test_keyword_highlight():
    style = SubtitleStyle(margin=200, size=60, keywords=["篮球", "AI"], keyword_color="#FFD60A", keyword_scale=1.2)
    (event,) = _events(style, "关于篮球，MAIN 和 AI 都行")
    assert "关于{\\1c&H0AD6FF&\\fs72}篮球{\\1c&HFFFFFF&\\fs60}，MAIN 和 {\\1c&H0AD6FF&\\fs72}AI{" in event
    karaoke = style.model_copy(update={"animation": "karaoke"})
    (event,) = _events(karaoke, "关于篮球")
    assert "{\\kf" in event and "\\fs72}篮{\\1c&H5CE4FF&\\fs60}" in event  # 卡拉 OK 时恢复成高亮色


def test_keyword_spans_prefer_longer_words():
    assert subs.keyword_spans("一半是梦想", ["梦想", "是梦想"]) == [(2, 5)]
    assert subs.keyword_spans("", ["x"]) == []


def test_keywords_are_cleaned():
    style = SubtitleStyle.model_validate({"keywords": [" 篮球 ", "篮球", "", "x" * 30]})
    assert style.keywords == ["篮球", "x" * 20]


def test_suggest_keywords_api(client, settings):
    from shotwright.providers import registry
    from shotwright.providers.base import ProviderSpec

    class KeywordLLM:
        def __init__(self, _settings):
            pass

        def complete(self, messages, *, json_mode=False):
            return json.dumps({"keywords": ["篮球", "不存在的词", "篮球", "梦想"]}, ensure_ascii=False)

    registry.register(ProviderSpec(kind="llm", id="keyworder", name="K", offline=True), KeywordLLM)
    settings.defaults["llm"] = "keyworder"
    created = client.post(
        "/api/v2/projects",
        json={"mode": "split", "script": "关于篮球，一半是梦想，一半是生活。", "voice": {"provider": "silent"}},
    ).json()
    result = client.post(f"/api/v2/projects/{created['id']}/subtitle/keywords").json()
    assert result["subtitle"]["keywords"] == ["篮球", "梦想"]
