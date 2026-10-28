"""素材准备：生成 AI 画面之前，全片设定里的主体要准备好，并由用户确认。

一个主体（人物、产品、物品）就绪的条件：
* 写了外观描述；
* 有至少一张参考图（上传的照片或 AI 定妆图），或者用户确认它不需要参考图；
* 有多张参考图时，每张都标了颜色 / 款式（不然模型分不清哪张是哪一款）。

场景类主体不需要参考图。确认之前不能生成 AI 视频、AI 图片；素材库检索、上传、纯色不受影响。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from shotwright.core.models import Look, LookSubject, Project

LOCKED_MESSAGE = "请先在「① 素材」里补齐并确认全片设定（主体的参考图和外观），再生成 AI 画面"


@dataclass
class SubjectStatus:
    code: str
    ready: bool
    missing: list[str] = field(default_factory=list)
    """缺什么：appearance（外观描述）、images（参考图）、labels（多张图的颜色标注）。"""


def subject_status(project: Project, subject: LookSubject) -> SubjectStatus:
    if subject.kind == "place":
        return SubjectStatus(subject.code, True)
    missing: list[str] = []
    if not subject.appearance.strip():
        missing.append("appearance")
    images = [image for image in subject.images if image in project.assets]
    if not images and not subject.no_reference:
        missing.append("images")
    if len(images) > 1 and subject.kind != "person" and any(not subject.image_labels.get(image) for image in images):
        missing.append("labels")
    return SubjectStatus(subject.code, not missing, missing)


def needs_confirmation(look: Look) -> bool:
    """有需要准备的主体（非场景）时才需要确认；没有主体的视频（例如纯文字科普）不拦。"""
    return any(subject.kind != "place" for subject in look.subjects)


def generation_locked(project: Project) -> bool:
    """是否还不能生成 AI 画面：有主体、而且还没确认（旧项目 ``confirmed`` 为空，不拦）。"""
    return project.look.confirmed is False and needs_confirmation(project.look)


def problems(project: Project) -> list[str]:
    """还没准备好的地方，用于确认时的报错。"""
    names = {"appearance": "外观描述", "images": "参考图", "labels": "每张图的颜色标注"}
    result = []
    for subject in project.look.subjects:
        status = subject_status(project, subject)
        if not status.ready:
            label = f"{subject.code}{' ' + subject.name if subject.name else ''}"
            result.append(f"{label} 缺{'、'.join(names[item] for item in status.missing)}")
    return result


def confirm(project: Project) -> None:
    """确认素材。没准备好时抛 ``ValueError``，说明缺什么。"""
    missing = problems(project)
    if missing:
        raise ValueError("素材还没准备好：" + "；".join(missing))
    project.look.confirmed = True
    project.look.revised_at = None


def _content(look: Look) -> str:
    return look.model_dump_json(exclude={"confirmed", "revised_at"})


def carry_over(old: Look, new: Look) -> Look:
    """客户端整体替换全片设定时，保留服务端维护的确认状态；确认之后改了内容，记下修改时间。"""
    new.confirmed = old.confirmed
    new.revised_at = old.revised_at
    if old.confirmed and _content(old) != _content(new):
        new.revised_at = time.time()
    return new
