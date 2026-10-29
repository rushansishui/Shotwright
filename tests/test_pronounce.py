"""读音纠正：配音时把易读错的词换成同音字，字幕仍显示原文。"""

import json
from pathlib import Path

from shotwright.core.models import Project, Pronunciation
from shotwright.media import subtitles as subs
from shotwright.providers import registry
from shotwright.providers.base import ProviderSpec, SpeechResult, WordTiming
from shotwright.providers.tts.silent import SilentTTS
from shotwright.services import pronounce

FONT = Path(__file__).resolve().parents[1] / "resources" / "fonts" / "NotoSansSC-Bold.ttf"


def test_builtin_rules_fix_fei_di_only_in_safe_context():
    project = Project()
    assert pronounce.spoken(project, "打着飞的也要赶回来比赛！") == "打着飞滴也要赶回来比赛！"
    assert pronounce.spoken(project, "周末坐飞的回家") == "周末坐飞滴回家"
    assert pronounce.spoken(project, "会飞的鸟") == "会飞的鸟"
    assert pronounce.spoken(project, "打的去机场，打的人很多") == "打滴去机场，打的人很多"
    assert pronounce.spoken(Project(language="en-US"), "打着飞的") == "打着飞的"


def test_user_entries_win_and_longest_first():
    project = Project(
        pronunciations=[
            Pronunciation(word="重庆", say="虫庆"),
            Pronunciation(word="银行行长", say="银航航长"),
            Pronunciation(word="行长", say="航长"),
        ]
    )
    said, mapping = pronounce.respell(project, "重庆银行行长说")
    assert said == "虫庆银航航长说"
    assert mapping == list(range(7))


def test_mapping_handles_different_lengths():
    project = Project(pronunciations=[Pronunciation(word="AI", say="诶爱")], language="en-US")
    said, mapping = pronounce.respell(project, "用AI做")
    assert said == "用诶爱做"
    assert mapping == [0, 1, 2, 3]
    project.pronunciations = [Pronunciation(word="一二三", say="X")]
    assert pronounce.respell(project, "零一二三四") == ("零X四", [0, 1, 4])


def test_subtitle_timing_aligns_to_respelled_words():
    project = Project()
    text = "要比赛，坐飞的回来"
    words = [("要比赛", 0.0, 0.5), ("坐飞滴", 1.0, 1.4), ("回来", 1.4, 1.8)]
    cues = subs.split_cues(
        text,
        0.0,
        3.0,
        font_path=FONT,
        size=64,
        max_width=420,
        max_lines=1,
        words=words,
        respell=lambda value: pronounce.respell(project, value),
    )
    assert [cue.text for cue in cues] == ["要比赛", "坐飞的回来"]  # 字幕仍是原文
    assert cues[1].start == 1.0  # 「坐飞滴」对回了原文的「坐飞的」


class RecordingTTS(SilentTTS):
    said: list[str] = []

    def __init__(self, _settings=None):
        pass

    def synthesize(self, text, *, voice, rate, volume, output):
        RecordingTTS.said.append(text)
        result = super().synthesize(text, voice=voice, rate=rate, volume=volume, output=output)
        return SpeechResult(path=result.path, duration=result.duration, words=[WordTiming(text, 0.0, 0.5)])


def test_voice_uses_respelled_text_and_preview(client, settings):
    registry.register(ProviderSpec(kind="tts", id="recorder", name="R", offline=True), RecordingTTS)
    created = client.post(
        "/api/v2/projects",
        json={"mode": "split", "script": "打着飞的也要赶回来比赛！", "voice": {"provider": "recorder"}},
    ).json()
    pid = created["id"]
    response = client.post(f"/api/v2/projects/{pid}/voice/preview", json={"text": "打着飞的也要赶回来"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("audio/")
    assert RecordingTTS.said[-1] == "打着飞滴也要赶回来"
    patched = client.patch(
        f"/api/v2/projects/{pid}", json={"pronunciations": [{"word": "比赛", "say": "比塞", "note": "测试"}]}
    ).json()
    assert patched["pronunciations"] == [{"word": "比赛", "say": "比塞", "note": "测试"}]
    client.post(f"/api/v2/projects/{pid}/voice/preview", json={"text": "要比赛"})
    assert RecordingTTS.said[-1] == "要比塞"
    assert client.post(f"/api/v2/projects/{pid}/voice/preview", json={"text": "[停顿]"}).status_code == 422


def test_suggest_pronunciations_api(client, settings):
    class Checker:
        def __init__(self, _settings):
            pass

        def complete(self, messages, *, json_mode=False):
            return json.dumps(
                {
                    "items": [
                        {"word": "飞的", "say": "飞滴", "note": "fēi dī"},
                        {"word": "不存在", "say": "布存在"},
                        {"word": "比赛", "say": "比赛"},
                        {"word": "飞的", "say": "飞滴"},
                    ]
                },
                ensure_ascii=False,
            )

    registry.register(ProviderSpec(kind="llm", id="checker", name="C", offline=True), Checker)
    settings.defaults["llm"] = "checker"
    created = client.post(
        "/api/v2/projects",
        json={"mode": "split", "script": "打着飞的也要赶回来比赛！", "voice": {"provider": "silent"}},
    ).json()
    result = client.post(f"/api/v2/projects/{created['id']}/pronunciations/suggest").json()
    assert result["pronunciations"] == [{"word": "飞的", "say": "飞滴", "note": "fēi dī"}]
    again = client.post(f"/api/v2/projects/{created['id']}/pronunciations/suggest").json()
    assert len(again["pronunciations"]) == 1  # 已有的不重复
