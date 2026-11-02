"""发布包：把成片、封面、字幕和发布用的文字打成一个 zip。

发布到短视频平台时除了视频本身，还要封面、字幕和标题 / 简介 / 话题标签。这些每次
都要手工准备，容易漏。这里按交付平台的规格导出成一份，直接拿去上传。

打包内容：

    标题-shortwright.zip
        video.mp4        成片（按交付平台编码的那一版）
        cover.jpg        封面（成片中间偏后的一帧，9:16 / 16:9 按项目画布）
        subtitles.srt    字幕（没有字幕文件时不放）
        publish.json     标题、简介、话题标签、平台、时长，给平台或人工填写用
        README.txt       一句话说明每个文件是什么

话题标签由大模型按文案挑；没配大模型时用视频标题和主体名兜底，保证包里不为空。
"""

from __future__ import annotations

import json
import logging
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from shotwright.core import platforms
from shotwright.core.models import Project
from shotwright.services import speech
from shotwright.services.archive import safe_name

log = logging.getLogger("shotwright.publish")

COVER_WIDTH = 1080
"""封面宽度：竖屏 1080×1920、横屏 1080×608 左右，够平台要求。"""


@dataclass
class PublishPack:
    """发布包里的文字信息。"""

    title: str
    description: str
    tags: list[str]
    platform: str
    duration: float
    aspect: str
    language: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "platform": self.platform,
            "duration": round(self.duration, 2),
            "aspect": self.aspect,
            "language": self.language,
        }


TAGS_PROMPT = """你是短视频运营。根据下面的视频文案，给这条短视频拟发布用的元数据，用{language}写。
- title：标题，12–24 个字，抓人但不夸张，不要用「最」「第一」这类绝对化说法。
- description：简介，一两句话（40–80 字），说清这条视频讲什么、看完能得到什么。
- tags：6–10 个话题标签，每个 2–8 个字，不带 #，覆盖品类、场景、人群。
只输出 JSON：{{"title": "...", "description": "...", "tags": ["..."]}}"""


def _fallback_tags(project: Project) -> list[str]:
    """没配大模型时的话题标签：视频标题、出现最多的主体名、视频类型。"""
    tags: list[str] = []
    for subject in project.look.subjects:
        if subject.kind != "place" and (subject.name or "").strip():
            tags.append(subject.name.strip()[:8])
    if project.video_type:
        from shotwright.core.models import VIDEO_TYPE_NAMES

        tags.append(VIDEO_TYPE_NAMES.get(project.video_type, "")[:8])
    if project.title.strip():
        tags.append(project.title.strip()[:8])
    return [tag for tag in dict.fromkeys(tags) if tag][:8]


def plan_pack(
    project: Project, *, llm=None, duration: float = 0.0, platform: str | None = None
) -> PublishPack:
    """准备好发布用的文字。``llm`` 为空或调用失败时用兜底文案。"""
    kind = platforms.platform(platform or project.delivery)
    script = "\n".join(speech.plain(shot.text) for shot in project.shots if shot.text.strip())
    headline = project.titles.headline.text.strip() or project.title.strip()
    title = headline[:80] or "未命名视频"
    description = script.strip().replace("\n", " ")[:120]
    tags = _fallback_tags(project)
    if llm is not None and script.strip():
        try:
            from shotwright.services.storyboard import _extract_json

            language = "简体中文" if project.language.lower().startswith("zh") else "English"
            data = _extract_json(
                llm.complete(
                    [
                        {"role": "system", "content": TAGS_PROMPT.format(language=language)},
                        {"role": "user", "content": script[:3000]},
                    ],
                    json_mode=True,
                )
            )
            title = str(data.get("title") or "").strip()[:80] or title
            description = str(data.get("description") or "").strip()[:200] or description
            found = [
                str(item).strip().lstrip("#")[:20] for item in data.get("tags") or [] if str(item).strip()
            ]
            if found:
                tags = list(dict.fromkeys(found))[:10]
        except Exception as exc:  # noqa: BLE001 - 文字模型失败不影响打包
            log.warning("发布包的标题和话题没有生成，用默认文案：%s", exc)
    return PublishPack(
        title=title,
        description=description,
        tags=tags,
        platform=kind.id,
        duration=duration,
        aspect=project.canvas.aspect,
        language=project.language,
    )


def build_pack(
    project: Project,
    *,
    video: Path,
    subtitles: Path | None,
    pack: PublishPack,
    target: Path,
) -> Path:
    """把成片、封面、字幕和 publish.json 打包成 zip。封面从成片中间偏后取一帧。"""
    from shotwright.media.ffmpeg import MediaError, extract_frame

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shotwright-publish-") as temp:
        work = Path(temp)
        cover = work / "cover.jpg"
        # 取中间偏后的一帧：开头多半是标题卡或空镜，中间偏后通常是最有信息量的一帧。
        try:
            at = max(0.0, pack.duration * 0.6)
            extract_frame(video, cover, at=at, width=COVER_WIDTH)
        except MediaError as exc:
            log.warning("封面截帧失败，发布包里不放封面：%s", exc)
            cover = None  # type: ignore[assignment]
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(video, "video.mp4")
            if cover is not None and cover.is_file():
                archive.write(cover, "cover.jpg")
            if subtitles is not None and subtitles.is_file():
                archive.write(subtitles, "subtitles.srt")
            archive.writestr(
                "publish.json",
                json.dumps(pack.to_dict(), ensure_ascii=False, indent=2),
            )
            archive.writestr("README.txt", _readme(pack))
    return target


def _readme(pack: PublishPack) -> str:
    return (
        "这是 Shotwright 导出的发布包。\n\n"
        "video.mp4      成片，按交付平台的编码参数导出。\n"
        "cover.jpg      封面，从成片中间偏后截的一帧，可以在平台上再换。\n"
        "subtitles.srt  字幕，平台支持上传字幕时用。\n"
        "publish.json   标题、简介、话题标签，方便复制到平台或交给工具。\n\n"
        f"平台：{platforms.platform(pack.platform).name}\n"
        f"标题：{pack.title}\n"
        f"话题：{' '.join('#' + tag for tag in pack.tags)}\n"
        f"时长：{pack.duration:.1f} 秒，画面比例 {pack.aspect}\n"
    )


def pack_name(pack: PublishPack) -> str:
    return f"{safe_name(pack.title or 'shotwright')}-shortwright.zip"


__all__ = ["PublishPack", "build_pack", "pack_name", "plan_pack"]
