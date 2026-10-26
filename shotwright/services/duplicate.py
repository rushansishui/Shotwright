"""复制项目：把一个已有的视频复制成新项目，在副本上继续编辑，原项目不受影响。

复制内容：全部设置、镜头、素材和渲染缓存（没改动的镜头渲染时直接复用）。不复制
历史成片、累计费用和未取回的收费任务。副本是独立项目，不属于原项目的语言版本组。
素材和缓存优先用硬链接（不占额外空间、几乎瞬间完成），不支持时退回复制文件。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from pathlib import Path

from shotwright.core.ids import new_id
from shotwright.core.models import Project
from shotwright.core.store import ProjectStore

log = logging.getLogger("shotwright.duplicate")

TITLE_LIMIT = 120


def _link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def copy_title(title: str, *, english: bool = False) -> str:
    suffix = " (copy)" if english else "（副本）"
    base = (title or ("Untitled" if english else "未命名项目")).strip()
    return (base[: TITLE_LIMIT - len(suffix)] + suffix).strip()


def duplicate_project(store: ProjectStore, project: Project, *, title: str | None = None) -> Project:
    """复制项目，返回新项目。"""
    copy = project.model_copy(deep=True)
    copy.id = new_id()
    # 标题里有中文时用「（副本）」，否则按项目语言选后缀。
    chinese = project.language.lower().startswith("zh") or bool(re.search(r"[一-鿿]", project.title))
    copy.title = (title or "").strip()[:TITLE_LIMIT] or copy_title(project.title, english=not chinese)
    copy.group = None
    copy.revision = 0
    copy.cost_credits = 0.0
    copy.created_at = copy.updated_at = time.time()
    for shot in copy.shots:
        shot.visual.pending = None
        shot.status, shot.error = "draft", None
    store.create(copy)

    copied = 0
    for asset in copy.assets.values():
        source = store.asset_path(project, asset.id)
        target = store.asset_path(copy, asset.id)
        if source.is_file() and not target.exists():
            _link_or_copy(source, target)
            copied += 1
    cache = store.project_dir(project.id) / "cache"
    if cache.is_dir():
        target_cache = store.project_dir(copy.id) / "cache"
        for source in cache.rglob("*"):
            if source.is_file() and not source.name.startswith(".tmp"):
                _link_or_copy(source, target_cache / source.relative_to(cache))
    log.info("项目 %s 复制为新项目 %s（%d 个素材）", project.id, copy.id, copied)
    return copy
