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
    first = store.add_asset(project, source, kind="image", source="upload", suffix=".png")
    second = store.add_asset(project, source, kind="image", source="upload", suffix=".png")
    assert first.id == second.id
    assert len(list(store.assets_dir(project.id).iterdir())) == 1
    assert store.asset_path(project, first.id).read_bytes() == b"hello"


def test_list_and_delete(store):
    a = store.create(Project(title="a"))
    store.create(Project(title="b"))
    assert {p.title for p in store.list()} == {"a", "b"}
    store.delete(a.id)
    assert [p.title for p in store.list()] == ["b"]
