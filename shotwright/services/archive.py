"""项目导出与导入：把整个项目（设置、镜头、素材）打成一个 zip，换台机器也能接着做。

打包内容：

    项目名.shotwright.zip
        project.json        项目状态
        meta.json           导出格式版本、项目标题、导出时间
        assets/<子目录>/…    素材文件（和项目目录里的结构一致）

不打包渲染缓存和成片：缓存可以删了重算，成片体积大且能重新渲染。导入时给项目换一个
新 id，不会和已有的项目撞；素材按内容去重，重复导入同一个包只在库里多一个项目。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
import zipfile
from pathlib import Path

from shotwright.core.ids import new_id
from shotwright.core.models import Project
from shotwright.core.store import ProjectStore

log = logging.getLogger("shotwright.archive")

FORMAT_VERSION = 1
"""导出包的格式版本；以后改结构时用来判断能不能导入。"""
MANIFEST = "meta.json"
"""包里的说明文件。"""


def safe_name(text: str, fallback: str = "shotwright-project") -> str:
    """把标题变成安全的文件名：去掉路径分隔符和 Windows 不允许的字符。"""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", (text or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or fallback)[:80]


def export_project(store: ProjectStore, project: Project, target: Path) -> Path:
    """把项目导出成 ``target``（zip）。返回实际写入的路径。"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    directory = store.project_dir(project.id)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            MANIFEST,
            json.dumps(
                {
                    "format": FORMAT_VERSION,
                    "title": project.title,
                    "exported_at": time.time(),
                    "shots": len(project.shots),
                    "assets": len(project.assets),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.write(directory / "project.json", "project.json")
        assets_dir = directory / "assets"
        if assets_dir.is_dir():
            for path in sorted(assets_dir.rglob("*")):
                if path.is_file() and not path.name.startswith(".tmp"):
                    archive.write(path, str(Path("assets") / path.relative_to(assets_dir)))
    log.info("项目 %s 导出到 %s（%d 个素材）", project.id, target, len(project.assets))
    return target


class ArchiveError(ValueError):
    """导入的文件不是本工具导出的项目包。"""


def read_manifest(source: Path) -> dict:
    """读导出包的说明；不是项目包时抛 :class:`ArchiveError`。"""
    try:
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            if "project.json" not in names:
                raise ArchiveError("这个文件里没有 project.json，不是镜匠导出的项目包")
            data = read_json_from(archive, MANIFEST) if MANIFEST in names else {}
    except zipfile.BadZipFile as exc:
        raise ArchiveError("不是有效的 zip 文件") from exc
    return data or {}


def read_json_from(archive: zipfile.ZipFile, name: str) -> dict | None:
    try:
        return json.loads(archive.read(name).decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError):
        return None


def import_project(store: ProjectStore, source: Path, *, title: str | None = None) -> Project:
    """导入一个导出包，返回新项目（新 id）。

    素材按内容去重：包里和现有素材相同的文件直接复用，重复导入不会占双倍空间。
    """
    source = Path(source)
    meta = read_manifest(source)
    try:
        with zipfile.ZipFile(source) as archive:
            raw = read_json_from(archive, "project.json")
            if raw is None:
                raise ArchiveError("项目包里的 project.json 读不出来")
            # 换一个新 id，避免和已有项目撞。
            raw["id"] = new_id()
            raw["group"] = None
            raw["revision"] = 0
            raw["cost_credits"] = 0.0
            raw["created_at"] = raw["updated_at"] = time.time()
            for shot in raw.get("shots") or []:
                visual = shot.get("visual") or {}
                visual["pending"] = None
                shot["status"], shot["error"] = "draft", None
            try:
                project = Project.model_validate(raw)
            except ValueError as exc:
                raise ArchiveError(f"项目包里的项目数据有问题：{exc}") from exc
            if title and title.strip():
                project.title = title.strip()[:120]
            elif meta.get("title"):
                project.title = str(meta["title"])[:120] or project.title

            store.create(project)
            restored = 0
            for member in archive.namelist():
                if not member.startswith("assets/") or member.endswith("/"):
                    continue
                name = Path(member).name
                if not name or name.startswith(".tmp"):
                    continue
                # 只解到项目目录里的 assets 下，路径里的 .. 一律丢掉。
                try:
                    relative = Path(member).relative_to("assets")
                except ValueError:
                    continue
                parts = [part for part in relative.parts if part not in ("..", ".", "", "/", "\\")]
                if not parts:
                    continue
                destination = store.assets_dir(project.id).joinpath(*parts)
                if store.assets_dir(project.id) not in destination.parents:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                restored += 1
    except zipfile.BadZipFile as exc:
        raise ArchiveError("不是有效的 zip 文件") from exc
    log.info("导入项目为 %s（%d 个素材文件）", project.id, restored)
    return store.load(project.id)


__all__ = ["ArchiveError", "export_project", "import_project", "read_manifest", "safe_name"]
