import json

import pytest
from pydantic import ValidationError

from shotwright.core.models import Canvas, Project, Shot, ShotVisual, SubtitleStyle
from shotwright.core.store import ProjectNotFound


def test_canvas_resolution():
    assert (Canvas(aspect="9:16").width, Canvas(aspect="9:16").height) == (1080, 1920)
    assert (Canvas(aspect="16:9").width, Canvas(aspect="16:9").height) == (1920, 1080)


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 65, "..", "a b"])
def test_ids_reject_path_tricks(bad):
    with pytest.raises(ValidationError):
        Shot(id=bad)
    with pytest.raises(ValidationError):
        ShotVisual(selected=bad)


def test_font_must_be_a_bare_filename():
    with pytest.raises(ValidationError):
        SubtitleStyle(font="../../etc/passwd")


def test_duplicate_shot_ids_rejected():
    with pytest.raises(ValidationError):
        Project(shots=[Shot(id="a"), Shot(id="a")])


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        Project.model_validate({"shots": [], "api_key": "secret"})


def test_store_roundtrip_and_revision(store):
    project = store.create(Project(title="t", shots=[Shot(text="一")]))
    assert project.revision == 1
    loaded = store.load(project.id)
    assert loaded.shots[0].text == "一"
    loaded.title = "新标题"
    store.save(loaded)
    assert store.load(project.id).revision == 2
    data = json.loads((store.project_dir(project.id) / "project.json").read_text(encoding="utf-8"))
    assert data["title"] == "新标题"


def test_store_missing_project(store):
    with pytest.raises(ProjectNotFound):
        store.load("missing")


def test_assets_are_deduplicated(store, tmp_path):
    project = store.create(Project())
    source = tmp_path / "a.bin"
    source.write_bytes(b"hello")
    first = store.add_asset(project, source, kind="image", source="upload", suffix=".png", folder="upload")
    second = store.add_asset(project, source, kind="image", source="upload", suffix=".png", folder="upload")
    assert first.id == second.id
    assert len(list((store.assets_dir(project.id) / "upload").iterdir())) == 1
    assert store.asset_path(project, first.id).read_bytes() == b"hello"


def test_list_and_delete(store):
    a = store.create(Project(title="a"))
    store.create(Project(title="b"))
    assert {p.title for p in store.list()} == {"a", "b"}
    store.delete(a.id)
    assert [p.title for p in store.list()] == ["b"]


# ---- 素材按来源分目录 ----------------------------------------------------------


def _legacy_asset(store, project, name: str, data: bytes, **fields):
    """模拟 v0.3 之前的项目：文件直接放在 assets/ 下，没有 folder。"""
    from shotwright.core.models import Asset

    directory = store.assets_dir(project.id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    asset = Asset(id=name.split(".")[0], filename=name, sha256="0" * 64, **fields)
    project.assets[asset.id] = asset
    return asset


def test_new_assets_go_to_their_folder(store, tmp_path):
    project = store.create(Project())
    for folder in ("ai-video", "ai-image", "stock", "upload"):
        source = tmp_path / f"{folder}.bin"
        source.write_bytes(folder.encode())
        asset = store.add_asset(project, source, kind="image", source="x", suffix=".png", folder=folder)
        path = store.asset_path(project, asset.id)
        assert path.parent == store.assets_dir(project.id) / folder
        assert path.read_bytes() == folder.encode()


def test_asset_folder_by_source():
    from shotwright.core.models import Asset
    from shotwright.services.visuals import asset_folder

    def make(kind, source):
        return Asset(id="a1", kind=kind, source=source, filename="a1.mp4", sha256="0" * 64)

    assert asset_folder(make("video", "pixabay")) == "stock"
    assert asset_folder(make("video", "pexels")) == "stock"
    assert asset_folder(make("video", "seedance")) == "ai-video"
    assert asset_folder(make("image", "seedream")) == "ai-image"
    assert asset_folder(make("image", "openai")) == "ai-image"
    assert asset_folder(make("audio", "upload")) == "upload"
    assert asset_folder(make("video", "upload")) == "upload"
    assert asset_folder(make("video", "something-else")) == "other"


def test_organize_moves_legacy_assets(store):
    from shotwright.services.visuals import organize_all

    project = store.create(Project())
    _legacy_asset(store, project, "aaaa.mp4", b"stock", kind="video", source="pixabay")
    _legacy_asset(store, project, "bbbb.mp4", b"ai", kind="video", source="seedance")
    _legacy_asset(store, project, "cccc.jpg", b"img", kind="image", source="seedream")
    _legacy_asset(store, project, "dddd.mp3", b"bgm", kind="audio", source="upload")
    store.save(project)

    assert organize_all(store) == 1
    loaded = store.load(project.id)
    expected = {"aaaa": "stock", "bbbb": "ai-video", "cccc": "ai-image", "dddd": "upload"}
    for asset_id, folder in expected.items():
        assert loaded.assets[asset_id].folder == folder
        path = store.asset_path(loaded, asset_id)
        assert path.parent.name == folder and path.is_file()
    assert not any(p.is_file() for p in store.assets_dir(project.id).iterdir())
    # 已整理的项目不再改动
    assert organize_all(store) == 0


def test_asset_path_finds_file_after_interrupted_move(store):
    project = store.create(Project())
    _legacy_asset(store, project, "eeee.mp4", b"x", kind="video", source="pixabay")
    store.save(project)
    moved = store.assets_dir(project.id) / "stock" / "eeee.mp4"
    moved.parent.mkdir(parents=True)
    (store.assets_dir(project.id) / "eeee.mp4").replace(moved)
    assert store.asset_path(store.load(project.id), "eeee") == moved.resolve()
