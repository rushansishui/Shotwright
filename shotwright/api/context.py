"""路由共用的上下文：项目存储、配置、渲染队列，以及改项目时的通用辅助。

``api/app.py`` 只负责创建应用、挂中间件和异常处理；各个接口按主题分在
``api/routes/`` 下的模块里，都通过这里的 :class:`ApiContext` 拿到运行时的依赖。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException

from shotwright.api.jobs import JobManager
from shotwright.config import Settings
from shotwright.core.models import Project, validate_id
from shotwright.core.store import ProjectNotFound, ProjectStore

log = logging.getLogger("shotwright.api")

MAX_UPLOAD = 500 * 1024 * 1024
"""上传文件的大小上限。"""
BGM_KEY = "__bgm__"
"""in_progress 里用这个伪镜头 id 表示「背景音乐检索中」。"""


@dataclass
class ApiContext:
    """一次应用运行期间的共享依赖。"""

    settings: Settings
    store: ProjectStore
    jobs: JobManager
    in_progress: dict[tuple[str, str], dict] = field(default_factory=dict)
    """正在进行的检索 / 生成：(项目 id, 镜头 id) → 进度信息。"""
    in_progress_lock: threading.Lock = field(default_factory=threading.Lock)

    def id(self, value: str) -> str:
        """校验并返回 id；不合法时报 400。"""
        try:
            return validate_id(value)
        except ValueError:
            raise HTTPException(400, "无效的 id") from None

    def mutate(self, project_id: str, change: Callable[[Project], object]) -> Project:
        """在项目锁内读取、修改、保存，避免并发编辑互相覆盖。"""
        with self.store.lock(self.id(project_id)):
            project = self.store.load(project_id)
            try:
                change(project)
                Project.model_validate(project.model_dump())
            except KeyError:
                raise HTTPException(404, "镜头或素材不存在") from None
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            return self.store.save(project)

    def clear_pending(self, project_id: str, shot_id: str) -> None:
        """清掉镜头里落盘的任务 id。

        生成成功与失败都会调用：成功后它已经没用，失败后留着会让界面一直显示"生成中"，
        而且下次启动还会拿它去轮询一个已经结束的任务。
        """
        try:
            with self.store.lock(project_id):
                latest = self.store.load(project_id)
                try:
                    target = latest.shot(shot_id)
                except KeyError:
                    return
                if target.visual.pending is None:
                    return
                target.visual.pending = None
                self.store.save(latest)
        except (ProjectNotFound, OSError) as exc:
            log.warning("清除镜头 %s 的任务标记失败：%s", shot_id, exc)
