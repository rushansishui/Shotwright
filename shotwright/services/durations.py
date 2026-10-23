from shotwright.core.models import Project, Shot
from shotwright.providers.tts.silent import estimate_seconds


def estimated_shot_seconds(project: Project, shot: Shot) -> float:
    """不调用配音服务时对镜头时长的估算，用于界面预览和素材筛选。"""
    if shot.duration:
        return shot.duration
    from shotwright.services import speech

    text = speech.plain(shot.text)
    return estimate_seconds(text, project.voice_for(shot).rate) + speech.pause_seconds(shot.text) + 0.3
