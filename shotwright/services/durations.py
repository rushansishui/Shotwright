from shotwright.core.models import Project, Shot
from shotwright.providers.tts.silent import estimate_seconds


def estimated_shot_seconds(project: Project, shot: Shot) -> float:
    """不调用配音服务时对镜头时长的估算，用于界面预览和素材筛选。"""
    if shot.duration:
        return shot.duration
    return estimate_seconds(shot.text, project.voice_for(shot).rate) + 0.3
