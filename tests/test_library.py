"""品牌包与主体库（services/library.py）。"""

from __future__ import annotations

import pytest

from shotwright.core.models import Look, LookSubject, Project, ReferenceAsset
from shotwright.core.store import ProjectStore
from shotwright.services.library import BrandPack, Library, SubjectTemplate


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / "projects")


def _image(tmp_path, name: str, colour: str) -> object:
    """生成一张纯色 PNG 当参考图（不需要 FFmpeg）。"""
    import struct
    import zlib

    path = tmp_path / name
    width = height = 4
    raw = b"".join(
        b"\x00" + bytes(int(colour[i : i + 2], 16) for i in range(0, 6, 2)) * width for _ in range(height)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    return path


def test_brand_pack_round_trip(library):
    pack = BrandPack(name="某品牌", color="#FF5500", font="NotoSansSC-Bold.ttf", grade="warm")
    library.save_brand(pack)
    found = library.brands()
    assert len(found) == 1
    assert found[0].name == "某品牌"
    assert found[0].color == "#FF5500"

    assert library.delete_brand(pack.id)
    assert library.brands() == []


def test_brand_from_project_and_apply(library, tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = Project(title="测试")
    project.subtitle = project.subtitle.model_copy(update={"color": "#123456"})
    project.look = Look(grade="cool")
    project.references.append(ReferenceAsset(asset_id="abc", role="logo", label="Logo"))
    project = store.create(project)

    pack, logo = library.brand_from_project(project, "品牌 A")
    assert pack.name == "品牌 A"
    assert pack.color == "#123456"
    assert pack.grade == "cool"
    # 没指定 Logo 时，取主体库里第一个 Logo 参考素材的 id（这个项目里的素材本体不存在，所以是 None）。
    assert logo is None

    # 套用到另一个项目：只改品牌包里填了的项。
    target = Project(title="另一个")
    target.look = Look(grade="natural")
    target = store.create(target)
    library.apply_brand(store, target, pack)
    assert target.subtitle.color == "#123456"
    assert target.look.grade == "cool"
    # 品牌包没填字体，保持原样。
    assert target.subtitle.font == "NotoSansSC-Bold.ttf"


def test_apply_brand_copies_logo_into_project(library, tmp_path):
    store = ProjectStore(tmp_path / "projects")
    source = Project(title="源")
    source = store.create(source)
    path = _image(tmp_path, "logo.png", "FF0000")
    asset = store.add_asset(source, path, kind="image", source="upload", folder="upload")
    source.references.append(ReferenceAsset(asset_id=asset.id, role="logo", label="Logo"))
    store.save(source)

    pack = library.save_brand_from_project(store, source, "带 Logo 的品牌")
    assert pack.logo

    target = Project(title="目标")
    target = store.create(target)
    library.apply_brand(store, target, pack)
    logos = [ref for ref in target.references if ref.role == "logo"]
    assert len(logos) == 1
    assert logos[0].asset_id in target.assets
    # 套用两次不会重复加。
    library.apply_brand(store, target, pack)
    assert len([ref for ref in target.references if ref.role == "logo"]) == 1


def test_subject_template_reuse_across_projects(library, tmp_path):
    store = ProjectStore(tmp_path / "projects")
    source = Project(title="源")
    source = store.create(source)
    path = _image(tmp_path, "product.png", "00AA00")
    asset = store.add_asset(source, path, kind="image", source="upload", folder="upload")
    source.look = Look(
        subjects=[
            LookSubject(
                code="P1",
                name="咖啡机",
                kind="product",
                appearance="哑光白色圆柱机身",
                images=[asset.id],
                image_labels={asset.id: "白色款"},
            )
        ]
    )
    store.save(source)

    item = library.save_subject_from_project(store, source, "P1")
    assert item.appearance == "哑光白色圆柱机身"
    assert item.images and item.image_labels.get(item.images[0]) == "白色款"

    target = Project(title="目标")
    target = store.create(target)
    code = library.apply_subject(store, target, item)
    assert code == "P1"
    subject = target.look.subjects[0]
    assert subject.appearance == "哑光白色圆柱机身"
    assert subject.images and subject.images[0] in target.assets
    assert subject.image_labels[subject.images[0]] == "白色款"


def test_subject_code_collision_gets_a_new_number(library, tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = Project(title="t")
    project.look = Look(subjects=[LookSubject(code="P1", name="已有")])
    project = store.create(project)
    code = library.apply_subject(store, project, SubjectTemplate(code="P1", name="另一个"))
    assert code == "P2"


def test_library_lives_beside_the_projects(tmp_path):
    lib = Library(tmp_path / "storage" / "projects")
    assert lib.root == (tmp_path / "storage" / "library")
