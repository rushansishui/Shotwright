import json

import pytest

from shotwright.config import DEFAULT_FONTS_DIR
from shotwright.core.models import SubtitleStyle
from shotwright.media import subtitles as subs
from shotwright.providers.base import ProviderError
from shotwright.services.storyboard import generate_storyboard, split_script

FONT = DEFAULT_FONTS_DIR / "NotoSansSC-Bold.ttf"


def test_split_script_groups_short_sentences():
    shots = split_script("你好。今天天气不错。我们去公园散步吧！路上还可以买一杯咖啡，顺便看看湖边的风景。")
    assert len(shots) >= 2
    assert "".join(s.text for s in shots).replace(" ", "") == (
        "你好。今天天气不错。我们去公园散步吧！路上还可以买一杯咖啡，顺便看看湖边的风景。"
    )


def test_split_script_english():
    shots = split_script(
        "AI is everywhere. It writes code. It paints pictures. It even makes videos for you."
    )
    assert all(s.text for s in shots)
    assert " ".join(s.text for s in shots).startswith("AI is everywhere.")


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, messages, *, json_mode=False):
        return self.reply


def test_generate_storyboard_parses_fenced_json():
    reply = (
        "```json\n"
        + json.dumps(
            {
                "title": "AI 日常",
                "shots": [{"text": "第一句", "terms": ["robot", "city"], "prompt": "a robot"}, {"text": ""}],
            },
            ensure_ascii=False,
        )
        + "\n```"
    )
    title, shots = generate_storyboard(FakeLLM(reply), "AI")
    assert title == "AI 日常"
    assert len(shots) == 1
    assert shots[0].visual.terms == ["robot", "city"]


def test_generate_storyboard_rejects_garbage():
    with pytest.raises(ProviderError):
        generate_storyboard(FakeLLM("抱歉，我不能"), "AI")


def test_wrap_balances_lines():
    lines = subs.wrap("人工智能正在改变我们的日常生活。", FONT, 64, 928)
    assert len(lines) == 2
    assert abs(len(lines[0]) - len(lines[1])) <= 2


def test_wrap_keeps_english_words_whole():
    lines = subs.wrap("AI quietly helps millions of people every single day", FONT, 64, 600)
    assert all(not line.startswith(" ") for line in lines)
    assert " ".join(lines).split() == [
        "AI",
        "quietly",
        "helps",
        "millions",
        "of",
        "people",
        "every",
        "single",
        "day",
    ]


def test_cues_cover_the_whole_span_in_order():
    cues = subs.split_cues(
        "从早上醒来的智能闹钟，到通勤路上的导航推荐，再到晚上帮你挑选电影的推荐算法！",
        10.0,
        16.0,
        font_path=FONT,
        size=64,
        max_width=928,
    )
    assert cues[0].start == 10.0 and cues[-1].end == 16.0
    assert all(a.end == b.start for a, b in zip(cues, cues[1:], strict=False))
    assert all(c.text.count("\n") <= 1 for c in cues)


def test_ass_escapes_override_braces():
    text = subs.to_ass(
        [subs.Cue(0, 1, "{\\b1}危险")], SubtitleStyle(), width=1080, height=1920, family="Noto Sans SC"
    )
    assert "\\{\\\\b1\\}危险" in text
    assert "PlayResY: 1920" in text


def test_font_family_is_read_from_file():
    assert subs.font_family(FONT) == "Noto Sans SC"


def test_llm_base_url_tolerates_full_endpoint():
    from shotwright.providers.llm.openai_compat import OpenAICompatibleLLM

    llm = OpenAICompatibleLLM(
        {"base_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions/", "model": "m"}
    )
    assert llm.base_url == "https://ark.cn-beijing.volces.com/api/v3"


def test_llm_presets_only_need_a_key(settings):
    from shotwright.providers import registry

    ids = [s.id for s in registry.specs("llm")]
    assert ids[:4] == ["doubao", "deepseek", "kimi", "qwen"] and "openai" in ids
    settings.providers["llm"] = {"deepseek": {"api_key": "k"}}
    llm = registry.create("llm", "deepseek", settings)
    assert llm.base_url == "https://api.deepseek.com" and llm.model == "deepseek-flash"
    settings.providers["llm"]["kimi"] = {"api_key": "k", "model": "kimi-custom"}
    assert registry.create("llm", "kimi", settings).model == "kimi-custom"
