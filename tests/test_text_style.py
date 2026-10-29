"""文字样式：加粗、倾斜、下划线、描边、阴影、底框、发光、字间距、对齐、入场效果、字体库。"""

import re
import shutil

from shotwright.core.models import Project, Shot, ShotVisual, SubtitleStyle, TitleSettings, VoiceSettings
from shotwright.media import fonts
from shotwright.media import subtitles as subs
from shotwright.pipeline.render import Renderer


def _fields(style_line: str) -> list[str]:
    return style_line.split(": ", 1)[1].split(",")


def test_style_flags_and_effects():
    style = SubtitleStyle(
        bold=False,
        italic=True,
        underline=True,
        spacing=4,
        shadow=3,
        shadow_color="#FF0000",
        shadow_opacity=0.5,
        opacity=0.8,
        align="left",
    )
    fields = _fields(
        subs.ass_style("Default", "Noto Sans SC", 64, style, subs.alignment("bottom", "left"), 60, 60, 90)
    )
    assert fields[7:10] == ["0", "-1", "-1"]  # 加粗关、倾斜、下划线
    assert fields[13] == "4"  # 字间距
    assert fields[15:18] == ["1", "3", "3"]  # 普通描边、描边 3、阴影 3
    assert fields[18] == "1"  # 左下
    assert fields[3].startswith("&H33")  # 不透明度 0.8
    assert fields[6] == "&H990000FF"  # 阴影颜色红色、0.5 × 0.8 不透明


def test_background_box_uses_border_style_3():
    style = SubtitleStyle(background="#222222", background_opacity=0.5)
    fields = _fields(subs.ass_style("Default", "F", 60, style, 2, 60, 60, 90))
    assert fields[15] == "3" and fields[16] == str(subs.box_padding(style, 60))
    assert fields[5] == "&H80222222"


def test_alignment_codes():
    assert subs.alignment("bottom", "center") == 2
    assert subs.alignment("top", "right") == 9
    assert subs.alignment("middle", "left") == 4
    assert subs.anchor(2, 1080, 1920, 60, 60, 170) == (540, 1750)
    assert subs.anchor(7, 1080, 1920, 60, 60, 160) == (60, 160)


def test_animations():
    base = SubtitleStyle()
    assert subs.styled_text(base, "你好", duration=2, size=64) == "你好"
    assert "\\fad(250,0)" in subs.styled_text(
        base.model_copy(update={"animation": "fade"}), "你好", duration=2, size=64
    )
    assert "\\t(0,180,\\fscx100\\fscy100)" in subs.styled_text(
        base.model_copy(update={"animation": "pop"}), "你好", duration=2, size=64
    )
    slide = subs.styled_text(
        base.model_copy(update={"animation": "slide_up"}), "你好", duration=2, size=64, point=(540, 1750)
    )
    assert "\\move(540,1782,540,1750,0,240)" in slide
    glow = subs.styled_text(base.model_copy(update={"glow": 4}), "你好", duration=2, size=64)
    assert glow.startswith("{\\blur4}")


def test_karaoke_and_typewriter_split_by_character():
    karaoke = subs.styled_text(SubtitleStyle(animation="karaoke"), "一起\n加油 go", duration=2.0, size=64)
    durations = [int(v) for v in re.findall(r"\\kf(\d+)", karaoke)]
    assert sum(durations) == 200 and len(durations) == 6  # 一 起 加 油 空格 go
    assert "\\N" in karaoke
    typed = subs.styled_text(SubtitleStyle(animation="typewriter"), "冠军", duration=3.0, size=64)
    assert typed.count("\\ko") == 2
    # 标题不支持卡拉 OK，按逐字打出处理。
    assert "\\ko" in subs.styled_text(
        SubtitleStyle(animation="karaoke"), "冠军", duration=3, size=64, allow_karaoke=False
    )


def test_karaoke_style_swaps_primary_and_secondary():
    style = SubtitleStyle(animation="karaoke", color="#FFFFFF", highlight_color="#FFE45C")
    fields = _fields(subs.ass_style("Default", "F", 60, style, 2, 60, 60, 90))
    assert fields[3] == "&H005CE4FF" and fields[4] == "&H00FFFFFF"


def test_spacing_wraps_earlier():
    font = fonts.find(_settings(), "NotoSansSC-Bold.ttf").path
    text = "再为自己年轻一次第九届峦山谷尖沙咀杯即将开幕"
    assert len(subs.wrap(text, font, 64, 900, spacing=20)) > len(subs.wrap(text, font, 64, 900))


def _settings():
    from shotwright.config import Settings

    return Settings()


def test_font_catalog_groups_weights():
    settings = _settings()
    families = {item["family"]: item for item in fonts.families(settings)}
    sans = families["Noto Sans SC"]
    assert sans["has_bold"] and set(sans["files"]) == {"NotoSansSC-Bold.ttf", "NotoSansSC-Regular.otf"}
    assert families["ZCOOL KuaiLe"]["name_zh"] == "站酷快乐体"
    bold = fonts.find(settings, "NotoSansSC-Bold.ttf")
    assert fonts.pick_weight(settings, bold, False).file == "NotoSansSC-Regular.otf"
    assert fonts.pick_weight(settings, bold, True).file == "NotoSansSC-Bold.ttf"
    kuaile = fonts.find(settings, "ZCOOLKuaiLe-Regular.ttf")
    assert fonts.pick_weight(settings, kuaile, True) == kuaile  # 没有粗体文件时由 libass 合成加粗


def test_old_title_settings_still_load():
    titles = TitleSettings.model_validate(
        {
            "headline": {
                "enabled": True,
                "text": "x",
                "size": 76,
                "color": "#FFFFFF",
                "outline_color": "#000000",
                "background": "#000000",
                "background_opacity": 0,
                "margin": 160,
            },
            "intro": {
                "enabled": False,
                "text": "",
                "subtitle": "",
                "duration": 2.5,
                "size": 110,
                "color": "#FFFFFF",
                "dim": 0.55,
            },
            "shot": {
                "size": 56,
                "color": "#FFE45C",
                "outline_color": "#000000",
                "position": "top",
                "margin": 330,
            },
        }
    )
    assert titles.headline.bold and titles.headline.outline == 5 and titles.headline.font is None
    assert titles.intro.animation == "fade" and titles.shot.animation == "fade"


def test_render_uses_regular_weight_and_title_font(store, settings, monkeypatch):
    captured: dict = {}
    original = subs.to_ass

    def spy(*args, **kwargs):
        text = original(*args, **kwargs)
        captured["ass"] = text
        return text

    monkeypatch.setattr(subs, "to_ass", spy)
    project = store.create(
        Project(
            voice=VoiceSettings(provider="silent"),
            shots=[Shot(text="第一句话。", title="开场", visual=ShotVisual(mode="color"))],
            loudness=None,
        )
    )
    project.subtitle.bold = False
    project.subtitle.italic = True
    project.subtitle.animation = "karaoke"
    project.titles.shot.font = "ZCOOLKuaiLe-Regular.ttf"
    project.titles.headline.enabled = True
    project.titles.headline.text = "大标题"
    project.titles.headline.font = "NotoSerifSC-Bold.otf"
    store.save(project)
    renderer = Renderer(store, settings)
    renderer.render(project.id)
    faces = renderer._text_faces(store.load(project.id))
    assert faces["subtitle"].file == "NotoSansSC-Regular.otf"
    assert faces["shot"].file == "ZCOOLKuaiLe-Regular.ttf"
    ass = captured["ass"]
    assert "Style: Default,Noto Sans SC,64," in ass and ",0,-1,0,0," in ass
    assert "Style: ShotTitle,ZCOOL KuaiLe," in ass and "Style: Headline,Noto Serif SC," in ass
    assert "\\kf" in ass


def test_font_api(client, settings, tmp_path):
    listed = client.get("/api/v2/fonts").json()
    assert any(item["family"] == "Noto Sans SC" for item in listed)
    assert client.get("/api/v2/fonts/ZCOOLKuaiLe-Regular.ttf").status_code == 200
    assert client.get("/api/v2/fonts/..%2Fsecret.ttf").status_code == 404
    bad = client.post("/api/v2/fonts", files={"file": ("x.ttf", b"not a font")})
    assert bad.status_code == 422
    source = settings.fonts_dir / "ZCOOLKuaiLe-Regular.ttf"
    copy = tmp_path / "my font.ttf"
    shutil.copyfile(source, copy)
    with copy.open("rb") as handle:
        created = client.post("/api/v2/fonts", files={"file": ("我的 字体.ttf", handle)})
    assert created.status_code == 201
    name = created.json()["file"]
    assert name.endswith(".ttf") and "/" not in name
    assert any(name in item["files"] and item["user"] is False for item in client.get("/api/v2/fonts").json())
    assert client.delete("/api/v2/fonts/ZCOOLKuaiLe-Regular.ttf").status_code == 422  # 内置字体不能删
    assert client.delete(f"/api/v2/fonts/{name}").status_code == 204
