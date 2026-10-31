"""项目导出与导入（services/archive.py）。"""

from __future__ import annotations

import zipfile

import pytest
from conftest import make_clip

from shotwright.core.models import Project, Shot, ShotVisual, VoiceSettings
from shotwright.services import archive


def _project(store, tmp_path) -> Project:
    project = Project(title="导出测试", voice=VoiceSettings(provider="silent"))
    project.shots = [
        Shot(text="第一个镜头。", visual=ShotVisual(mode="color", color="#AA0000")),
        Shot(text="第二个镜头。", visual=ShotVisual(mode="upload")),
    ]
    store.create(project)
    clip = make_clip(tmp_path / "clip.mp4", seconds=1.0, size="320x568")
    asset = store.add_asset(project, clip, kind="video", source="upload", folder="upload")
    project.shots[1].visual.selected = asset.id
    project.shots[1].visual.candidates = [asset.id]
    return store.save(project)


def test_export_writes_project_and_assets(store, tmp_path):
    project = _project(store, tmp_path)
    target = tmp_path / "out.zip"
    archive.export_project(store, project, target)

    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    assert "project.json" in names
    assert archive.MANIFEST in names
    assert any(name.startswith("assets/upload/") for name in names)
    # 渲染缓存和成片不打包。
    assert not any(name.startswith("cache/") or name.startswith("renders/") for name in names)

    meta = archive.read_manifest(target)
    assert meta["title"] == "导出测试"
    assert meta["shots"] == 2


def test_import_gives_a_new_project_with_assets(store, tmp_path):
    project = _project(store, tmp_path)
    target = tmp_path / "out.zip"
    archive.export_project(store, project, target)

    imported = archive.import_project(store, target)
    assert imported.id != project.id
    assert imported.title == "导出测试"
    assert len(imported.shots) == 2
    # 素材跟着过来了，能按项目里的记录找到文件。
    for asset in imported.assets.values():
        assert store.asset_path(imported, asset.id).is_file()
    # 收费任务和累计费用不沿用。
    assert imported.cost_credits == 0.0
    assert all(shot.visual.pending is None for shot in imported.shots)


def test_import_twice_makes_two_projects(store, tmp_path):
    project = _project(store, tmp_path)
    target = tmp_path / "out.zip"
    archive.export_project(store, project, target)

    first = archive.import_project(store, target)
    second = archive.import_project(store, target)
    assert first.id != second.id
    assert len(store.list()) == 3  # 原项目 + 两个导入


def test_import_can_rename(store, tmp_path):
    project = _project(store, tmp_path)
    target = tmp_path / "out.zip"
    archive.export_project(store, project, target)
    imported = archive.import_project(store, target, title="换一个名字")
    assert imported.title == "换一个名字"


def test_import_rejects_a_non_project_zip(store, tmp_path):
    bogus = tmp_path / "not-a-project.zip"
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("readme.txt", "hello")
    with pytest.raises(archive.ArchiveError, match="不是镜匠导出的项目包"):
        archive.import_project(store, bogus)


def test_import_rejects_a_broken_zip(store, tmp_path):
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip at all")
    with pytest.raises(archive.ArchiveError, match="不是有效的 zip"):
        archive.import_project(store, broken)


def test_import_ignores_path_traversal(store, tmp_path):
    """包里的路径带 .. 时，只解到项目目录里，不会写到外面去。"""
    project = _project(store, tmp_path)
    target = tmp_path / "out.zip"
    archive.export_project(store, project, target)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(target) as src, zipfile.ZipFile(evil, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr("assets/../../escaped.txt", "pwned")

    imported = archive.import_project(store, evil)
    assert not (tmp_path / "escaped.txt").exists()
    assert store.exists(imported.id)
    assert not (store.root.parent / "escaped.txt").exists()


def test_safe_name_strips_unsafe_characters():
    assert archive.safe_name("我的 视频/测试") == "我的 视频测试"
    assert archive.safe_name('a:b*c?d"e<f>g|h') == "abcdefgh"
    assert archive.safe_name("   ") == "shotwright-project"
    assert len(archive.safe_name("长" * 200)) == 80
