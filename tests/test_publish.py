"""发布包（services/publish.py）。"""

from __future__ import annotations

import json
import zipfile

from conftest import make_clip

from shotwright.core.models import Look, LookSubject, Project, Shot, ShotVisual, VoiceSettings
from shotwright.services import publish


def _project(**kwargs) -> Project:
    project = Project(title="咖啡机推荐", voice=VoiceSettings(provider="silent"), **kwargs)
    project.shots = [
        Shot(text="早上总是手忙脚乱。[停顿]这台机器三分钟出咖啡。", visual=ShotVisual(mode="color")),
        Shot(text="充一次电能做十五杯。", visual=ShotVisual(mode="color")),
    ]
    project.look = Look(
        subjects=[LookSubject(code="P1", name="便携咖啡机", kind="product", appearance="白色圆柱机身")]
    )
    return project


def test_plan_pack_falls_back_without_llm():
    pack = publish.plan_pack(_project())
    assert pack.title == "咖啡机推荐"
    # 简介取文案，停顿标注被去掉。
    assert "[停顿]" not in pack.description
    assert "三分钟出咖啡" in pack.description
    # 话题标签用主体名和视频类型兜底，不会为空。
    assert "便携咖啡机" in pack.tags


def test_plan_pack_uses_llm_when_available():
    class FakeLLM:
        def complete(self, messages, json_mode=False):
            return json.dumps(
                {
                    "title": "三分钟喝上现磨咖啡",
                    "description": "通勤族的早晨救星。",
                    "tags": ["#咖啡", "通勤好物", "咖啡机"],
                },
                ensure_ascii=False,
            )

    pack = publish.plan_pack(_project(), llm=FakeLLM(), duration=30.0)
    assert pack.title == "三分钟喝上现磨咖啡"
    assert pack.description == "通勤族的早晨救星。"
    # 标签去掉 # 前缀，顺序保持。
    assert pack.tags == ["咖啡", "通勤好物", "咖啡机"]
    assert pack.duration == 30.0


def test_plan_pack_survives_a_broken_llm():
    class BrokenLLM:
        def complete(self, messages, json_mode=False):
            raise RuntimeError("模型挂了")

    pack = publish.plan_pack(_project(), llm=BrokenLLM())
    assert pack.title == "咖啡机推荐"
    assert pack.tags


def test_build_pack_contains_video_cover_subtitles_and_json(tmp_path):
    project = _project()
    video = make_clip(tmp_path / "video.mp4", seconds=2.0, size="320x568")
    subtitles = tmp_path / "subtitles.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n第一句\n", encoding="utf-8")
    pack = publish.PublishPack(
        title="咖啡机推荐",
        description="简介",
        tags=["咖啡"],
        platform="douyin",
        duration=2.0,
        aspect="9:16",
        language="zh-CN",
    )
    target = tmp_path / "pack.zip"
    publish.build_pack(project, video=video, subtitles=subtitles, pack=pack, target=target)

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        data = json.loads(archive.read("publish.json"))
        readme = archive.read("README.txt").decode("utf-8")
    assert {"video.mp4", "subtitles.srt", "publish.json", "README.txt"} <= names
    assert "cover.jpg" in names
    assert data["title"] == "咖啡机推荐"
    assert data["tags"] == ["咖啡"]
    assert "封面" in readme


def test_build_pack_works_without_subtitles(tmp_path):
    project = _project()
    video = make_clip(tmp_path / "video.mp4", seconds=1.0, size="320x568")
    pack = publish.plan_pack(project, duration=1.0)
    target = tmp_path / "pack.zip"
    publish.build_pack(project, video=video, subtitles=None, pack=pack, target=target)
    with zipfile.ZipFile(target) as archive:
        assert "subtitles.srt" not in archive.namelist()
        assert "video.mp4" in archive.namelist()


def test_pack_name_is_a_safe_filename():
    pack = publish.plan_pack(_project())
    name = publish.pack_name(pack)
    assert name.endswith(".zip")
    assert "/" not in name and ":" not in name
