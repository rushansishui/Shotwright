"""跨项目复用的素材：品牌包和主体库。

做同一条产品线、同一个品牌的视频时，Logo、品牌色、字体、片尾样式、产品照片和
外观描述每做一个视频都要重新传一遍。这里把它们存在所有项目之外，一键套用到新项目。

存放在 ``<storage_dir 的上一级>/library/``：

    library/
        brands.json        品牌包列表（样式是 JSON，Logo 是按内容命名的图片）
        subjects.json      主体库（外观描述是 JSON，参考图按内容命名）
        files/             两边的图片都放这里，按内容去重

品牌包和主体库都只存图片文件名，不存项目里的素材 id——同一个 Logo 在哪个项目里
都是同一份文件。套用到项目时按内容去重复制进项目的 assets，和用户自己上传的图片一样处理。
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from shotwright.core.ids import new_id
from shotwright.core.models import (
    MAX_SUBJECT_IMAGES,
    LookSubject,
    Project,
    ReferenceAsset,
)
from shotwright.core.store import ProjectStore, atomic_write_text, read_json, sha256_file, write_json

log = logging.getLogger("shotwright.library")

MAX_BRANDS = 40
MAX_SUBJECTS = 200


@dataclass
class BrandPack:
    """一个品牌的视觉规范：Logo、品牌色、字体、字幕和标题样式、调色。"""

    id: str = field(default_factory=new_id)
    name: str = ""
    logo: str | None = None
    """Logo 图片的文件名（存在 ``library/files/`` 下）；为空表示这个品牌包没有 Logo。"""
    color: str = "#FFFFFF"
    """品牌主色：用在字幕、叠加文字和标题上（``#RRGGBB``）。"""
    font: str | None = None
    """字幕和标题的字体文件名；为空时保持项目原来的字体。"""
    grade: str = ""
    """统一调色预设，取值同 :data:`shotwright.core.models.GradePreset`；为空表示不改。"""
    subtitle: dict = field(default_factory=dict)
    """字幕样式的覆盖项（``TextStyle`` 里的颜色、描边等字段）。"""
    titles: dict = field(default_factory=dict)
    """标题样式的覆盖项：``headline`` / ``intro`` / ``shot`` 各一份。"""
    cta: str = ""
    """片尾的行动引导文字，例如「点击下方链接购买」；为空表示不加。"""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "logo": self.logo,
            "color": self.color,
            "font": self.font,
            "grade": self.grade,
            "subtitle": self.subtitle,
            "titles": self.titles,
            "cta": self.cta,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BrandPack:
        return cls(
            id=str(data.get("id") or new_id())[:64],
            name=str(data.get("name") or "")[:60],
            logo=data.get("logo") or None,
            color=str(data.get("color") or "#FFFFFF"),
            font=data.get("font") or None,
            grade=str(data.get("grade") or ""),
            subtitle=dict(data.get("subtitle") or {}),
            titles=dict(data.get("titles") or {}),
            cta=str(data.get("cta") or "")[:120],
            created_at=float(data.get("created_at") or time.time()),
        )


@dataclass
class SubjectTemplate:
    """主体库里的一份主体：外观描述和参考图，新项目里可以直接复用。"""

    id: str = field(default_factory=new_id)
    code: str = "P1"
    name: str = ""
    kind: str = "product"
    appearance: str = ""
    images: list[str] = field(default_factory=list)
    """参考图文件名（存在 ``library/files/`` 下）。"""
    image_labels: dict[str, str] = field(default_factory=dict)
    """参考图说明：文件名 → 颜色 / 款式。"""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "kind": self.kind,
            "appearance": self.appearance,
            "images": self.images,
            "image_labels": self.image_labels,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SubjectTemplate:
        return cls(
            id=str(data.get("id") or new_id())[:64],
            code=str(data.get("code") or "P1")[:20],
            name=str(data.get("name") or "")[:60],
            kind=str(data.get("kind") or "product"),
            appearance=str(data.get("appearance") or "")[:600],
            images=[str(item) for item in data.get("images") or [] if item][:MAX_SUBJECT_IMAGES],
            image_labels={str(k): str(v)[:40] for k, v in (data.get("image_labels") or {}).items()},
            created_at=float(data.get("created_at") or time.time()),
        )


class Library:
    """品牌包和主体库的读写。构造时给项目的存储根目录。"""

    def __init__(self, storage_dir: Path):
        # 项目都在 storage/projects/<id>，素材库放在 Projects 的上一级，跟着项目一起备份。
        self.root = Path(storage_dir).parent / "library"
        self.files = self.root / "files"

    def _file(self, name: str) -> Path:
        return self.root / name

    def _ensure(self) -> None:
        self.files.mkdir(parents=True, exist_ok=True)

    def _add_file(self, path: Path) -> str:
        """把图片放进库目录，按内容命名；同一个文件只存一份。返回文件名。"""
        self._ensure()
        path = Path(path)
        digest = sha256_file(path)
        suffix = path.suffix.lower()
        if not suffix or len(suffix) > 8 or not suffix[1:].isalnum():
            suffix = ".jpg"
        name = f"{digest[:16]}{suffix}"
        target = self.files / name
        if not target.is_file():
            shutil.copyfile(path, target)
        return name

    def file_path(self, name: str | None) -> Path | None:
        """库里的图片文件；不存在时返回 None。"""
        if not name:
            return None
        target = self.files / Path(name).name
        return target if target.is_file() else None

    # ---- 品牌包 -------------------------------------------------------------

    def brands(self) -> list[BrandPack]:
        data = read_json(self._file("brands.json")) or {}
        packs = [BrandPack.from_dict(item) for item in data.get("brands") or []]
        return sorted(packs, key=lambda item: item.created_at, reverse=True)

    def save_brand(self, pack: BrandPack) -> BrandPack:
        packs = [item for item in self.brands() if item.id != pack.id]
        packs.insert(0, pack)
        self._ensure()
        write_json(self._file("brands.json"), {"brands": [item.to_dict() for item in packs[:MAX_BRANDS]]})
        return pack

    def brand(self, brand_id: str) -> BrandPack | None:
        return next((item for item in self.brands() if item.id == brand_id), None)

    def delete_brand(self, brand_id: str) -> bool:
        packs = self.brands()
        kept = [item for item in packs if item.id != brand_id]
        if len(kept) == len(packs):
            return False
        write_json(self._file("brands.json"), {"brands": [item.to_dict() for item in kept]})
        return True

    def brand_from_project(
        self, project: Project, name: str, logo_asset: str | None = None
    ) -> tuple[BrandPack, str | None]:
        """把一个项目当前的样式存成品牌包（从现有项目「存为品牌包」）。

        返回（品牌包, 要用作 Logo 的素材 id）。没指定 Logo 时用主体库里第一个 Logo 参考素材。
        """
        pack = BrandPack(
            name=name.strip()[:60] or project.title[:60] or "未命名品牌",
            color=project.subtitle.color,
            font=project.subtitle.font or None,
            grade=project.look.grade,
            subtitle={"color": project.subtitle.color, "outline_color": project.subtitle.outline_color},
            titles={
                "headline": project.titles.headline.model_dump(exclude={"text", "candidates"}),
                "shot": project.titles.shot.model_dump(),
            },
        )
        if logo_asset is None:
            logo = next(
                (
                    ref
                    for ref in project.references
                    if ref.role == "logo" and ref.enabled and ref.asset_id in project.assets
                ),
                None,
            )
            logo_asset = logo.asset_id if logo else None
        return pack, logo_asset

    def _store_logo(self, store: ProjectStore, project: Project, asset_id: str | None) -> str | None:
        if not asset_id or asset_id not in project.assets:
            return None
        try:
            return self._add_file(store.asset_path(project, asset_id))
        except OSError as exc:
            log.warning("品牌包 Logo 保存失败：%s", exc)
            return None

    def save_brand_from_project(
        self, store: ProjectStore, project: Project, name: str, logo_asset: str | None = None
    ) -> BrandPack:
        pack, logo_id = self.brand_from_project(project, name, logo_asset)
        pack.logo = self._store_logo(store, project, logo_id)
        pack.cta = next(
            (item.text for item in project.overlays if item.enabled and item.text.strip()),
            "",
        )[:120]
        return self.save_brand(pack)

    # ---- 主体库 -------------------------------------------------------------

    def subjects(self) -> list[SubjectTemplate]:
        data = read_json(self._file("subjects.json")) or {}
        items = [SubjectTemplate.from_dict(item) for item in data.get("subjects") or []]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def save_subject(self, subject: SubjectTemplate) -> SubjectTemplate:
        items = [item for item in self.subjects() if item.id != subject.id]
        items.insert(0, subject)
        self._ensure()
        write_json(
            self._file("subjects.json"), {"subjects": [item.to_dict() for item in items[:MAX_SUBJECTS]]}
        )
        return subject

    def delete_subject(self, subject_id: str) -> bool:
        items = self.subjects()
        kept = [item for item in items if item.id != subject_id]
        if len(kept) == len(items):
            return False
        write_json(self._file("subjects.json"), {"subjects": [item.to_dict() for item in kept]})
        return True

    def save_subject_from_project(
        self, store: ProjectStore, project: Project, code: str, name: str = ""
    ) -> SubjectTemplate:
        """把全片设定里的一个主体存进主体库（连同参考图）。"""
        subject = next((item for item in project.look.subjects if item.code == code), None)
        if subject is None:
            raise KeyError(code)
        images: list[str] = []
        labels: dict[str, str] = {}
        for asset_id in subject.images:
            if asset_id not in project.assets:
                continue
            try:
                filename = self._add_file(store.asset_path(project, asset_id))
            except OSError as exc:
                log.warning("主体 %s 的参考图保存失败：%s", code, exc)
                continue
            images.append(filename)
            label = subject.image_labels.get(asset_id)
            if label:
                labels[filename] = label
        return self.save_subject(
            SubjectTemplate(
                code=subject.code,
                name=name.strip()[:60] or subject.name,
                kind=subject.kind,
                appearance=subject.appearance,
                images=images,
                image_labels=labels,
            )
        )

    # ---- 套用到项目 ---------------------------------------------------------

    def apply_brand(self, store: ProjectStore, project: Project, pack: BrandPack) -> None:
        """把品牌包套用到项目：品牌色、字体、调色、Logo 和片尾引导。

        只套用品牌包里填了的项；没填的保持项目原样，避免把用户已经调好的样式冲掉。
        """
        if pack.color and pack.color.startswith("#"):
            project.subtitle = project.subtitle.model_copy(update={"color": pack.color})
            project.titles.shot = project.titles.shot.model_copy(update={"color": pack.color})
        if pack.font:
            project.subtitle = project.subtitle.model_copy(update={"font": pack.font})
            project.titles.headline = project.titles.headline.model_copy(update={"font": pack.font})
            project.titles.intro = project.titles.intro.model_copy(update={"font": pack.font})
        if pack.grade:
            project.look.grade = pack.grade  # type: ignore[assignment]
        subtitle_fields = type(project.subtitle).model_fields
        for key, value in (pack.subtitle or {}).items():
            if key in subtitle_fields:
                project.subtitle = project.subtitle.model_copy(update={key: value})
        for section, patch in (pack.titles or {}).items():
            target = getattr(project.titles, section, None)
            if target is None or not isinstance(patch, dict):
                continue
            allowed = {k: v for k, v in patch.items() if k in type(target).model_fields}
            setattr(project.titles, section, target.model_copy(update=allowed))
        logo = self.file_path(pack.logo)
        if logo is not None:
            asset = store.add_asset(project, logo, kind="image", source="upload", folder="upload")
            if not any(ref.asset_id == asset.id for ref in project.references):
                project.references.append(ReferenceAsset(asset_id=asset.id, role="logo", label=pack.name))
        if pack.cta.strip() and not project.overlays:
            from shotwright.core.models import Overlay

            project.overlays.append(Overlay(text=pack.cta.strip(), position="bottom", size=48))

    def apply_subject(self, store: ProjectStore, project: Project, template: SubjectTemplate) -> str:
        """把主体库里的主体加进项目全片设定（参考图复制进项目）。返回最终用的代号。"""
        code = template.code
        existing = {item.code for item in project.look.subjects}
        if code in existing:
            # 代号被占用时顺延到下一个可用编号，免得覆盖项目里已有的主体。
            prefix = "".join(ch for ch in code if ch.isalpha()) or "O"
            used = [
                int(item[len(prefix) :])
                for item in existing
                if item.startswith(prefix) and item[len(prefix) :].isdigit()
            ]
            code = f"{prefix}{max(used, default=0) + 1}"
        images: list[str] = []
        labels: dict[str, str] = {}
        for filename in template.images:
            path = self.file_path(filename)
            if path is None:
                continue
            asset = store.add_asset(project, path, kind="image", source="upload", folder="upload")
            images.append(asset.id)
            if template.image_labels.get(filename):
                labels[asset.id] = template.image_labels[filename]
        project.look.subjects.append(
            LookSubject(
                code=code,
                name=template.name,
                kind=template.kind,  # type: ignore[arg-type]
                appearance=template.appearance,
                images=images[:MAX_SUBJECT_IMAGES],
                image_labels=labels,
            )
        )
        return code


def save_library_file(path: Path, target: Path) -> Path:
    """把一份文件原子地写到 ``target``（上传用）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    return target


__all__ = [
    "BrandPack",
    "Library",
    "SubjectTemplate",
    "atomic_write_text",
    "save_library_file",
]
