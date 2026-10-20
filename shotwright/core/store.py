"""项目存储：每个项目一个目录。

    <root>/<project_id>/
        project.json        项目状态（原子写入）
        assets/             素材，文件名为 <sha256 前 16 位><扩展名>，自动去重
        cache/              渲染缓存，按指纹命名，可随时删除
        renders/<id>/       每次渲染的产出

所有路径都通过 :meth:`ProjectStore.project_dir` 和 :meth:`asset_path` 解析，
并校验结果仍在项目目录内。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from shotwright.core.models import Asset, AssetKind, Project, validate_id


class ProjectNotFound(KeyError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


class ProjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    # ---- 路径 ---------------------------------------------------------------

    def project_dir(self, project_id: str) -> Path:
        path = (self.root / validate_id(project_id)).resolve()
        if path.parent != self.root:
            raise ValueError("项目路径越界")
        return path

    def _inside(self, project_id: str, *parts: str) -> Path:
        base = self.project_dir(project_id)
        path = base.joinpath(*parts).resolve()
        if base != path and base not in path.parents:
            raise ValueError("路径越界")
        return path

    def assets_dir(self, project_id: str) -> Path:
        return self._inside(project_id, "assets")

    def cache_dir(self, project_id: str, kind: str) -> Path:
        path = self._inside(project_id, "cache", validate_id(kind))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def render_dir(self, project_id: str, render_id: str) -> Path:
        path = self._inside(project_id, "renders", validate_id(render_id))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def asset_path(self, project: Project, asset_id: str) -> Path:
        asset = project.assets[validate_id(asset_id)]
        return self._inside(project.id, "assets", asset.filename)

    def lock(self, project_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(project_id, threading.RLock())

    # ---- 项目 ---------------------------------------------------------------

    def exists(self, project_id: str) -> bool:
        return (self.project_dir(project_id) / "project.json").is_file()

    def create(self, project: Project) -> Project:
        directory = self.project_dir(project.id)
        if (directory / "project.json").exists():
            raise FileExistsError(project.id)
        (directory / "assets").mkdir(parents=True, exist_ok=True)
        return self.save(project)

    def load(self, project_id: str) -> Project:
        path = self.project_dir(project_id) / "project.json"
        if not path.is_file():
            raise ProjectNotFound(project_id)
        return Project.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, project: Project) -> Project:
        with self.lock(project.id):
            project.revision += 1
            project.updated_at = time.time()
            atomic_write_text(
                self.project_dir(project.id) / "project.json",
                project.model_dump_json(indent=2),
            )
        return project

    def list(self) -> list[Project]:
        projects = []
        for child in sorted(self.root.iterdir()):
            if (child / "project.json").is_file():
                try:
                    projects.append(self.load(child.name))
                except (ValueError, OSError):
                    continue
        projects.sort(key=lambda item: item.updated_at, reverse=True)
        return projects

    def delete(self, project_id: str) -> None:
        directory = self.project_dir(project_id)
        if not (directory / "project.json").is_file():
            raise ProjectNotFound(project_id)
        shutil.rmtree(directory)

    # ---- 素材 ---------------------------------------------------------------

    def add_asset(
        self,
        project: Project,
        source_file: Path,
        *,
        kind: AssetKind,
        source: str,
        suffix: str | None = None,
        width: int | None = None,
        height: int | None = None,
        duration: float | None = None,
        meta: dict[str, str] | None = None,
        move: bool = False,
    ) -> Asset:
        """把文件放进项目素材目录并登记到 ``project.assets``。

        内容相同（SHA-256 相同）的文件只存一份，返回已有的素材记录。
        调用方负责随后 :meth:`save` 项目。
        """
        source_file = Path(source_file)
        digest = sha256_file(source_file)
        asset_id = digest[:16]
        if asset_id in project.assets:
            if move:
                source_file.unlink(missing_ok=True)
            return project.assets[asset_id]
        ext = (suffix or source_file.suffix or "").lower()
        if ext and (not ext.startswith(".") or len(ext) > 8 or not ext[1:].isalnum()):
            raise ValueError(f"不支持的扩展名：{ext}")
        filename = f"{asset_id}{ext}"
        target = self._inside(project.id, "assets", filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(source_file), target)
        else:
            shutil.copyfile(source_file, target)
        asset = Asset(
            id=asset_id,
            kind=kind,
            source=source,
            filename=filename,
            sha256=digest,
            width=width,
            height=height,
            duration=duration,
            meta=meta or {},
        )
        project.assets[asset_id] = asset
        return asset


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, value: object) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
