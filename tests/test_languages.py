import json

from conftest import make_clip

from shotwright.core import languages
from shotwright.core.models import Project, Shot, ShotVisual, VoiceSettings
from shotwright.media import subtitles as subs
from shotwright.pipeline.render import Renderer
from shotwright.services import localize, storyboard


def test_normalize_accepts_legacy_names():
    assert languages.normalize("简体中文") == "zh-CN"
    assert languages.normalize("auto") == "zh-CN"
    assert languages.normalize("en") == "en-US"
    assert languages.normalize("EN-gb") == "en-GB"
    assert languages.normalize("fr") == "fr-FR"
    assert languages.voice_language("ko-KR-SunHiNeural") == "ko-KR"


def test_tokens_keep_words_and_combining_marks():
    assert subs._tokens("Hello world") == ["Hello ", "world"]
    thai = subs._tokens("ต้นไม้")
    assert all(token[0] not in "้ั" for token in thai)  # 声调和元音不单独成行首
    assert subs._tokens("안녕하세요 반갑습니다") == ["안녕하세요 ", "반갑습니다"]


def test_font_fallback_for_korean_and_thai(settings):
    sc = settings.fonts_dir / languages.SC_FONT
    assert subs.pick_font(sc, settings.fonts_dir, "안녕하세요")[0].name == languages.KR_FONT
    assert subs.pick_font(sc, settings.fonts_dir, "สวัสดี")[0].name == languages.THAI_FONT
    assert subs.pick_font(sc, settings.fonts_dir, "你好 hello")[0].name == languages.SC_FONT


def test_render_korean_switches_font_with_warning(store, settings):
    shot = Shot(text="안녕하세요. 반갑습니다.", visual=ShotVisual(mode="color"))
    project = store.create(Project(voice=VoiceSettings(provider="silent"), shots=[shot]))
    result = Renderer(store, settings).render(project.id)
    assert any(languages.KR_FONT in w for w in result.warnings)


def test_apply_language_switches_voice_and_font():
    project = Project(shots=[Shot(text="hi", voice=VoiceSettings(voice="zh-CN-YunxiNeural", rate=1.2))])
    localize.apply_language(project, "ko-KR")
    assert project.language == "ko-KR"
    assert project.voice.voice == "ko-KR-SunHiNeural"
    assert project.shots[0].voice.voice == "ko-KR-SunHiNeural" and project.shots[0].voice.rate == 1.2
    assert project.subtitle.font == languages.KR_FONT
    silent = Project(voice=VoiceSettings(provider="silent", voice="x"))
    localize.apply_language(silent, "en-US")
    assert silent.voice.voice == "x"  # 非 Edge 配音不改声音


def test_split_script_english_and_japanese():
    shots = storyboard.split_script("This is version 2.5 of the app. It works well! Try it now.")
    assert "2.5" in shots[0].text
    jp = storyboard.split_script(
        "今日はいい天気です。散歩に行きましょう。公園で会いましょう。駅の前で待っています。"
    )
    assert len(jp) >= 2


class FakeLLM:
    def __init__(self):
        self.calls = []

    def complete(self, messages, *, json_mode=False):
        self.calls.append(messages)
        data = json.loads(messages[-1]["content"])
        return json.dumps(
            {
                "title": "EN " + data["title"],
                "shots": [{"id": s["id"], "text": "EN " + s["text"]} for s in data["shots"]],
            }
        )


def test_language_version_shares_visuals(store, settings, tmp_path):
    project = store.create(Project(title="标题", shots=[Shot(text="第一句。"), Shot(text="第二句。")]))
    clip = store.add_asset(project, make_clip(tmp_path / "c.mp4"), kind="video", source="upload")
    project.shots[0].visual = ShotVisual(mode="upload", selected=clip.id, candidates=[clip.id], locked=True)
    store.save(project)

    llm = FakeLLM()
    version = localize.create_language_version(store, llm, project, "en-US")
    assert "English" in llm.calls[0][0]["content"]
    assert version.language == "en-US" and version.group == project.id
    assert [s.text for s in version.shots] == ["EN 第一句。", "EN 第二句。"]
    assert [s.id for s in version.shots] == [s.id for s in project.shots]
    assert version.shots[0].visual.locked and version.voice.voice == "en-US-JennyNeural"
    assert store.asset_path(version, clip.id).is_file()
    assert {p.language for p in localize.versions(store, project)} == {"zh-CN", "en-US"}

    import pytest

    from shotwright.providers.base import ProviderError

    with pytest.raises(ProviderError, match="已经有"):
        localize.create_language_version(store, llm, project, "en-US")
