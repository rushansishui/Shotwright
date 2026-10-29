"""镜头级增量渲染。

阶段与依赖（详见 docs/ARCHITECTURE.md）：

    每个镜头：画面准备 → 配音 → 镜头渲染（含时长计算）
    整片：    拼接 → 字幕与背景音乐 → 导出

每个阶段的产出按指纹缓存在 ``cache/<阶段>/<指纹>.*``。重新渲染时，输入没变的阶段
直接复用缓存：只改了一个镜头的文案，就只会重做这个镜头的配音和渲染，再加上整片的
拼接与导出。增量渲染的思路参考了 MoneyPrinterTurbo 的 ``video_project.py``。
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from shotwright.config import Settings
from shotwright.core import platforms
from shotwright.core.fingerprint import fingerprint
from shotwright.core.ids import new_id
from shotwright.core.models import Project, Shot, auto_subtitle_margin
from shotwright.core.store import ProjectStore, read_json, sha256_file, write_json
from shotwright.media import beats
from shotwright.media import fonts as font_lib
from shotwright.media import subtitles as subs
from shotwright.media.ffmpeg import (
    LOCAL_PROTOCOLS,
    MediaCancelled,
    MediaError,
    loudnorm_stats,
    probe,
    run_ffmpeg,
)
from shotwright.media.grades import GRADE_VERSION, grade_filter
from shotwright.providers.base import ProviderError
from shotwright.services import pronounce, speech, visuals

from .metadata import tag_video
from .render_types import (
    AUDIO_ARGS,
    AUDIO_MIX,
    TRUE_PEAK,
    VOICE_PADDING,
    RenderCancelled,
    RenderError,
    RenderResult,
    ShotTiming,
    StageRecord,
)
from .shots import _video_args, render_shot
from .voice import synthesize_shot

# Re-export so existing importers keep working.
__all__ = [
    "RenderError",
    "RenderCancelled",
    "RenderResult",
    "ShotTiming",
    "StageRecord",
    "Renderer",
]

log = logging.getLogger("shotwright.render")

Progress = Callable[[float, str], None]

Quality = Literal["draft", "final"]


def _overlay_end(item) -> float:
    """叠加图片的结束时间；不限时长时给一个足够大的数。"""
    return item.start + item.duration if item.duration else 1e6


SAFE_GAP = 24
"""字幕和叠加文字离安全区边缘再留的距离（像素）。"""


def side_margins(project: Project) -> tuple[int, int]:
    """字幕左右留白：至少 60 像素，交付平台的安全区更宽时用安全区。"""
    _top, _bottom, left, right = platforms.safe_margins(
        project.delivery, project.canvas.width, project.canvas.height
    )
    return max(60, left + SAFE_GAP // 2), max(60, right + SAFE_GAP // 2)


def safe_margin(project: Project, position: str, margin: int) -> int:
    """把顶部 / 底部的边距挪进交付平台的安全区；居中的不动。"""
    top, bottom, _left, _right = platforms.safe_margins(
        project.delivery, project.canvas.width, project.canvas.height
    )
    if position == "top":
        return max(margin, top + SAFE_GAP)
    if position == "bottom":
        return max(margin, bottom + SAFE_GAP)
    return margin


def subtitle_margin(project: Project) -> int:
    """字幕实际的边距：没设置时按画面比例自动，再挪进交付平台的安全区。"""
    style = project.subtitle
    margin = style.margin
    if margin is None:
        margin = auto_subtitle_margin(project.canvas.aspect, project.canvas.height)
    return safe_margin(project, style.position, margin)


def shot_title_margin(project: Project) -> int:
    """镜头小标题的边距：挪进安全区；和常驻大标题都在顶部时放到大标题下面。"""
    style = project.titles.shot
    margin = safe_margin(project, style.position, style.margin)
    headline = project.titles.headline
    if style.position == "top" and headline.enabled and headline.text.strip():
        lines = headline.text.strip().count("\n") + 1
        below = safe_margin(project, "top", headline.margin) + round(headline.size * 1.35 * lines) + 24
        margin = max(margin, below)
    return margin


def _overlay_position(item, project: Project) -> tuple[str, str]:
    """图片叠加的位置表达式（ffmpeg overlay 的 x / y）。"""
    margin = safe_margin(project, item.position, item.margin)
    x = "(W-w)/2"
    if item.position == "top":
        y = f"{margin}"
    elif item.position == "bottom":
        y = f"H-h-{margin}"
    else:
        y = "(H-h)/2"
    return x, y


class Renderer:
    def __init__(
        self,
        store: ProjectStore,
        settings: Settings,
        *,
        progress: Progress | None = None,
        cancel: threading.Event | None = None,
        allow_paid: bool = False,
        quality: Quality = "final",
    ):
        self.store = store
        self.allow_paid = allow_paid
        """用户是否已确认本次渲染可以调用收费的 AI 生成。"""
        self.quality = quality
        """草稿档用快速编码参数，成片档用正常质量。"""
        self.settings = settings
        self._progress = progress or (lambda value, message: None)
        self._cancel = cancel or threading.Event()
        self.stages: list[StageRecord] = []
        self.warnings: list[str] = []
        self._faces: dict[str, font_lib.FontFace] | None = None

    # ---- 工具 ---------------------------------------------------------------

    def _report(self, value: float, message: str) -> None:
        if self._cancel.is_set():
            raise RenderCancelled("渲染已取消")
        self._progress(max(0.0, min(1.0, value)), message)

    def _stage(self, key: str, kind: str, fp: str, output: Path, build: Callable[[Path], None]) -> Path:
        """缓存命中则复用；否则在临时文件里生成，成功后再原子地移到位。"""
        started = time.monotonic()
        if output.is_file() and output.stat().st_size > 0:
            self.stages.append(StageRecord(key, kind, fp, True, 0.0))
            return output
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_name(f".tmp-{new_id(6)}{output.suffix}")
        try:
            build(temp)
            temp.replace(output)
        except MediaCancelled as exc:
            raise RenderCancelled(str(exc)) from exc
        finally:
            temp.unlink(missing_ok=True)
        self.stages.append(StageRecord(key, kind, fp, False, round(time.monotonic() - started, 3)))
        return output

    # ---- 入口 ---------------------------------------------------------------

    def render(self, project_id: str, render_id: str | None = None) -> RenderResult:
        render_id = render_id or new_id()
        project = self.store.load(project_id)
        if not project.shots:
            raise RenderError("项目里还没有镜头")
        self._report(0.0, "准备画面")
        self._prepare_visuals(project)

        total = len(project.shots)
        voices: list[tuple[str, Path, float, list[tuple[str, float, float]]]] = []
        for index, shot in enumerate(project.shots):
            self._report(0.10 + 0.25 * index / total, f"配音 {index + 1}/{total}")
            voices.append(
                synthesize_shot(
                    self.store,
                    self.settings,
                    project,
                    shot,
                    stages=self.stages,
                    cancel=self._cancel,
                )
            )

        durations, overlap = self._plan_durations(project, [voice[2] for voice in voices])
        durations = self._snap_to_beats(project, durations, overlap)
        shot_files: list[tuple[str, Path]] = []
        timings: list[ShotTiming] = []
        cursor = 0.0
        for index, (shot, (voice_fp, voice_path, voice_duration, words)) in enumerate(
            zip(project.shots, voices, strict=True)
        ):
            self._report(0.35 + 0.45 * index / total, f"渲染镜头 {index + 1}/{total}")
            duration = durations[index]
            fp, path = render_shot(
                self.store,
                project,
                shot,
                voice_fp,
                voice_path,
                duration,
                stages=self.stages,
                cancel=self._cancel,
                quality=self.quality,
            )
            shot_files.append((fp, path))
            timings.append(ShotTiming(shot.id, round(cursor, 3), voice_duration, duration, words))
            # 有转场时相邻镜头重叠 overlap 秒，下一个镜头要提前开始，字幕和配音才对得上。
            cursor += duration - overlap if index < total - 1 else duration

        self._report(0.80, "拼接镜头")
        assembly_fp, assembly = self._assemble(project, shot_files, overlap)

        self._report(0.85, "合成字幕与背景音乐")
        cues = self._cues(project, timings)
        cuts = [timing.start for timing in timings[1:]]
        shot_titles = [
            (shot.title, timing.start, timing.start + timing.duration)
            for shot, timing in zip(project.shots, timings, strict=True)
            if shot.title.strip()
        ]
        final = self._final(project, assembly_fp, assembly, cues, cuts, shot_titles)

        output_dir = self.store.render_dir(project.id, render_id)
        video_path = output_dir / "video.mp4"
        shutil.copyfile(final, video_path)
        self._report(0.95, "写入元数据")
        tag_video(project, video_path)
        srt_path = None
        if cues:
            srt_path = output_dir / "subtitles.srt"
            srt_path.write_text(subs.to_srt(cues), encoding="utf-8")
        info = probe(video_path)
        inspection: list[dict] = []
        if self.quality == "final":
            self._report(0.97, "成片抽帧检查")
            inspection = self._inspect(video_path, timings)
        result = RenderResult(
            render_id=render_id,
            project_id=project.id,
            revision=project.revision,
            video=video_path.name,
            subtitles=srt_path.name if srt_path else None,
            duration=round(info.duration or cursor, 3),
            timings=timings,
            stages=self.stages,
            warnings=self.warnings,
            inspection=inspection,
        )
        write_json(output_dir / "render.json", result.to_dict())
        self._report(1.0, "完成")
        return result

    def _plan_durations(self, project: Project, voice_durations: list[float]) -> tuple[list[float], float]:
        """算出每个镜头的时长和相邻镜头的重叠秒数（转场时长，硬切为 0）。

        有转场时，前一个镜头的结尾会和后一个镜头的开头叠在一起。为了不让配音互相
        压住，没有手动设时长的镜头尾部至少留出转场那么长的空白。任何一个镜头比转场
        还短时整片改为硬切，时间轴和拼接用同一条规则，字幕不会错位。
        """
        transition = project.transition
        d = transition.duration if transition.kind != "none" and len(project.shots) > 1 else 0.0

        def plan(padding: float) -> list[float]:
            return [
                round(shot.duration or voice + padding, 3)
                for shot, voice in zip(project.shots, voice_durations, strict=True)
            ]

        if d <= 0:
            return plan(VOICE_PADDING), 0.0
        durations = plan(max(VOICE_PADDING, d))
        if any(duration <= d + 0.05 for duration in durations):
            self.warnings.append("有镜头比转场时长还短，已改为硬切拼接")
            return plan(VOICE_PADDING), 0.0
        return durations, d

    def _snap_to_beats(self, project: Project, durations: list[float], overlap: float) -> list[float]:
        """打开了节拍对齐时，把镜头切换点就近挪到背景音乐的拍点上。手动设了时长的镜头不动。"""
        bgm = project.assets.get(project.bgm.asset_id or "")
        if not project.bgm.beat_sync or bgm is None or bgm.kind != "audio":
            return durations
        cache = self.store.cache_dir(project.id, "beats") / f"{bgm.sha256}.json"
        data = read_json(cache)
        if data is None:
            try:
                beats_list, bpm = beats.detect_beats(self.store.asset_path(project, bgm.id))
            except MediaError as exc:
                self.warnings.append(f"背景音乐节拍检测失败，没有对齐：{exc}")
                return durations
            data = {"beats": beats_list, "bpm": bpm}
            write_json(cache, data)
        return beats.snap_cuts(
            durations,
            overlap,
            [float(b) for b in data.get("beats") or []],
            fixed=[bool(shot.duration) for shot in project.shots],
        )

    # ---- 画面 ---------------------------------------------------------------

    def _prepare_visuals(self, project: Project) -> None:
        """为还没有画面的镜头自动检索或生成一次素材。

        AI 镜头只有在 ``allow_paid`` 为真时才会提交收费任务；未确认或生成失败时，
        有检索词就改用素材检索，仍然拿不到才退回纯色画面。每种情况都记录提示。
        """
        if not self.settings.auto_fetch_visuals:
            return
        changed: dict[str, tuple[list[str], str | None, list]] = {}
        for index, shot in enumerate(project.shots):
            visual = shot.visual
            if (
                visual.locked
                or visual.mode not in ("stock", "ai", "image")
                or self._has_visual(project, shot)
            ):
                continue
            try:
                try:
                    visuals.refresh_candidates(
                        self.store,
                        self.settings,
                        project,
                        shot,
                        confirm_charge=self.allow_paid,
                        quality=self.quality,
                    )
                except ProviderError as exc:
                    if visual.mode == "stock" or not visual.terms:
                        raise
                    visuals.fetch_stock_candidates(self.store, self.settings, project, shot)
                    self.warnings.append(f"镜头 {index + 1} 的 AI 画面没有生成（{exc}），已改用素材检索")
                changed[shot.id] = (list(visual.candidates), visual.selected, list(visual.takes))
            except ProviderError as exc:
                self.warnings.append(f"镜头 {index + 1} 没有拿到素材，已使用纯色画面：{exc}")
        if not changed:
            return
        with self.store.lock(project.id):
            latest = self.store.load(project.id)
            for asset_id, asset in project.assets.items():
                latest.assets.setdefault(asset_id, asset)
            for shot_id, (candidates, selected, takes) in changed.items():
                try:
                    target = latest.shot(shot_id)
                except KeyError:
                    continue
                if target.visual.selected is None:
                    target.visual.candidates = candidates
                    target.visual.selected = selected
                    target.visual.takes = takes
            self.store.save(latest)
        project.revision = latest.revision

    def _has_visual(self, project: Project, shot: Shot) -> bool:
        selected = shot.visual.selected
        return bool(
            selected and selected in project.assets and project.assets[selected].kind in ("video", "image")
        )

    # ---- 整片 ---------------------------------------------------------------

    def _assemble(self, project: Project, shots: list[tuple[str, Path]], overlap: float) -> tuple[str, Path]:
        kind = project.transition.kind if overlap > 0 else "none"
        fp = fingerprint(
            "assembly",
            [item[0] for item in shots],
            kind,
            round(overlap, 3) if kind != "none" else None,
        )
        output = self.store.cache_dir(project.id, "assembly") / f"{fp}.mp4"

        def build(temp: Path) -> None:
            if kind != "none":
                self._assemble_xfade(project, shots, temp, kind, overlap)
            else:
                self._assemble_concat(shots, temp)

        self._stage("assembly", "assembly", fp, output, build)
        return fp, output

    def _assemble_concat(self, shots: list[tuple[str, Path]], temp: Path) -> None:
        """硬切拼接：各镜头编码参数一致，可以直接 -c copy。"""
        listing = temp.with_suffix(".txt")
        lines = []
        for _, path in shots:
            escaped = path.resolve().as_posix().replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            run_ffmpeg(
                [
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-protocol_whitelist",
                    LOCAL_PROTOCOLS,
                    "-i",
                    str(listing),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    "-f",
                    "mp4",
                    str(temp),
                ],
                cancel=self._cancel,
            )
        finally:
            listing.unlink(missing_ok=True)

    _XFADE = {"fade": "fade", "slide": "slideleft", "wipe": "wipeleft"}

    def _assemble_xfade(
        self, project: Project, shots: list[tuple[str, Path]], temp: Path, kind: str, d: float
    ) -> None:
        """带转场的拼接：逐对叠加 xfade，必须重新编码（不能 -c copy）。

        是否用转场已在 ``_plan_durations`` 里决定，这里按同样的重叠秒数拼接。
        音频不做交叉淡化：每个镜头的音轨按它在时间轴上的起点延迟后直接混合。
        镜头尾部已留出至少 d 秒空白，配音不会叠在一起，也不会被淡出。
        """
        name = self._XFADE.get(kind, "fade")
        widths: list[float] = []
        for _, path in shots:
            try:
                widths.append(probe(path).duration or 0.0)
            except MediaError as exc:
                raise RenderError(f"读取镜头时长失败：{exc}") from exc

        args: list[str] = []
        for _, path in shots:
            args += ["-protocol_whitelist", LOCAL_PROTOCOLS, "-i", str(path)]
        chain: list[str] = ["[0:v]format=yuv420p[s0]"]
        audio_chain: list[str] = ["[0:a]aresample=48000,aformat=channel_layouts=stereo[a0]"]
        start = 0.0
        previous = "s0"
        for index in range(1, len(shots)):
            start += widths[index - 1] - d
            label = f"s{index}"
            chain.append(
                f"[{previous}][{index}:v]xfade=transition={name}:duration={d:.3f}:offset={start:.3f},"
                f"format=yuv420p[{label}]"
            )
            previous = label
            delay = max(0, round(start * 1000))
            audio_chain.append(
                f"[{index}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={delay}:all=1[a{index}]"
            )
        mix_inputs = "".join(f"[a{index}]" for index in range(len(shots)))
        audio_chain.append(f"{mix_inputs}amix=inputs={len(shots)}:duration=longest:normalize=0[aout]")
        run_ffmpeg(
            [
                *args,
                "-filter_complex",
                ";".join([*chain, *audio_chain]),
                "-map",
                f"[{previous}]",
                "-map",
                "[aout]",
                *_video_args(project.canvas.fps, self.quality),
                *AUDIO_ARGS,
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(temp),
            ],
            cancel=self._cancel,
        )

    def _text_faces(self, project: Project) -> dict[str, font_lib.FontFace]:
        """字幕和三种标题各自用的字体文件（已按「加粗」挑好字重）。

        标题没选字体时跟随字幕字体；字体缺字时（例如中文字体显示韩文）换成能显示的内置字体并提示。
        """
        if self._faces is not None:
            return self._faces
        titles = project.titles
        subtitle_font = font_lib.find(self.settings, project.subtitle.font)
        if subtitle_font is None:
            raise RenderError(f"找不到字幕字体：{project.subtitle.font}")
        script = "".join(speech.plain(shot.text) for shot in project.shots)
        texts = {
            "subtitle": (project.subtitle, script),
            "headline": (titles.headline, titles.headline.text),
            "intro": (
                titles.intro,
                titles.intro.text + titles.intro.subtitle + titles.headline.text + project.title,
            ),
            "shot": (titles.shot, "".join(shot.title for shot in project.shots)),
        }
        fonts_dir = self.settings.fonts_dir.resolve()
        faces: dict[str, font_lib.FontFace] = {}
        for kind, (style, text) in texts.items():
            face = subtitle_font
            if kind != "subtitle" and style.font:
                face = font_lib.find(self.settings, style.font) or subtitle_font
                if face is subtitle_font:
                    self.warnings.append(f"找不到标题字体 {style.font}，已改用字幕字体")
            face = font_lib.pick_weight(self.settings, face, style.bold)
            if text.strip():
                chosen, missing = subs.pick_font(face.path, fonts_dir, text)
                if chosen != face.path:
                    fallback = font_lib.find(self.settings, chosen.name)
                    if fallback is not None:
                        self.warnings.append(f"字体 {face.file} 缺少部分文字，已改用 {chosen.name}")
                        face = font_lib.pick_weight(self.settings, fallback, style.bold)
                if missing:
                    self.warnings.append(
                        f"有字体无法显示的文字：{missing[:20]}，请在 resources/fonts 中加入支持该语言的字体"
                    )
            faces[kind] = face
        self._faces = faces
        return faces

    def _font_path(self, project: Project) -> Path:
        """字幕字体文件（叠加文字和 AI 角标也用它）。"""
        return self._text_faces(project)["subtitle"].path

    def _cues(self, project: Project, timings: list[ShotTiming]) -> list[subs.Cue]:
        style = project.subtitle
        if not style.enabled:
            return []
        face = self._text_faces(project)["subtitle"]
        font = face.path
        left, right = side_margins(project)
        max_width = min(int(project.canvas.width * 0.86), project.canvas.width - left - right)
        if style.background_opacity > 0:
            max_width -= 2 * subs.box_padding(style, style.size)
        if style.italic:
            max_width -= style.size // 4
        if style.bold and not face.bold:
            max_width = int(max_width * 0.96)  # 合成加粗会让字略宽
        if style.background_opacity <= 0:
            max_width -= round(2 * style.outline2)
        if style.keywords:
            max_width -= round(
                style.size * (style.keyword_scale - 1) * 4
            )  # 关键词放大后行会变宽，按 4 个字预留
        cues: list[subs.Cue] = []
        for shot, timing in zip(project.shots, timings, strict=True):
            if not shot.text.strip():
                continue
            spoken = min(timing.voice_duration, timing.duration)
            cues.extend(
                subs.split_cues(
                    speech.plain(shot.text),
                    timing.start,
                    timing.start + spoken,
                    font_path=font,
                    size=style.size,
                    max_width=max_width,
                    words=timing.words,
                    spacing=style.spacing,
                    # 原声镜头没有经过读音纠正，时间戳直接对应原文。
                    respell=None
                    if shot.speech == "original"
                    else (lambda text: pronounce.respell(project, text)),
                )
            )
        return cues

    def _normalize(self, project: Project, source: Path, output: Path) -> None:
        """两遍 loudnorm 把整片响度统一到项目设定值：先测量，再按测量值线性调整。

        线性模式只整体调音量、不做动态压缩，配音和音乐的起伏保持原样；
        真峰值超出上限时 loudnorm 会自动退回动态模式压住峰值。静音的片子不处理。
        """
        stats = None
        if project.loudness is not None:
            stats = loudnorm_stats(source, target=project.loudness, true_peak=TRUE_PEAK)
        if stats is None:
            shutil.copyfile(source, output)
            return
        measured = ":".join(
            f"measured_{key[6:]}={value}" for key, value in stats.items() if key.startswith("input_")
        )
        audio = (
            f"loudnorm=I={project.loudness:g}:TP={TRUE_PEAK:g}:LRA=11:{measured}:"
            f"offset={stats['target_offset']}:linear=true,aresample=48000"
        )
        run_ffmpeg(
            [
                "-protocol_whitelist",
                LOCAL_PROTOCOLS,
                "-i",
                str(source),
                "-map",
                "0:v",
                "-map",
                "0:a",
                "-c:v",
                "copy",
                "-af",
                audio,
                *AUDIO_ARGS,
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(output.resolve()),
            ],
            cancel=self._cancel,
        )

    def _inspect(self, video: Path, timings: list[ShotTiming]) -> list[dict]:
        """成片抽帧检查（配置了看图模型时）：发现的问题同时写进提示，方便在界面上看到。"""
        from shotwright.services import inspect

        try:
            found = inspect.inspect_final(
                self.settings, video, [t.start for t in timings], [t.duration for t in timings]
            )
        except MediaError as exc:
            log.warning("成片抽帧检查失败：%s", exc)
            return []
        for item in found:
            self.warnings.append(f"成片检查：镜头 {item['shot']}：{'；'.join(item['issues'])}")
        return found

    def _whoosh(self, project: Project) -> Path:
        """转场音效：用滤过的粉红噪声合成一声短促的「嗖」，按版本缓存。"""
        path = self.store.cache_dir(project.id, "sfx") / f"{WHOOSH_VERSION}.wav"
        if not path.exists():
            run_ffmpeg(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "anoisesrc=d=0.6:c=pink:r=48000:a=0.5",
                    "-af",
                    "highpass=f=400,lowpass=f=6500,afade=t=in:st=0:d=0.3:curve=exp,"
                    "afade=t=out:st=0.3:d=0.3,volume=0.9",
                    "-ac",
                    "2",
                    str(path),
                ],
                cancel=self._cancel,
            )
        return path

    def _final(
        self,
        project: Project,
        assembly_fp: str,
        assembly: Path,
        cues: list[subs.Cue],
        cuts: list[float] | None = None,
        shot_titles: list[tuple[str, float, float]] | None = None,
    ) -> Path:
        cuts = cuts or []
        titles = project.titles
        has_titles = bool(
            shot_titles
            or (titles.headline.enabled and titles.headline.text.strip())
            or (titles.intro.enabled and (titles.intro.text or titles.headline.text or project.title).strip())
        )
        style = project.subtitle
        overlays = [o for o in project.overlays if o.enabled and (o.text.strip() or o.image)]
        # 文字叠加走 ASS；图片叠加（Logo）ASS 渲染不了，用 ffmpeg overlay 滤镜。
        text_overlays = [o for o in overlays if o.text.strip()]
        image_overlays = [o for o in overlays if not o.text.strip() and o.image]
        bgm_asset = project.assets.get(project.bgm.asset_id) if project.bgm.asset_id else None
        if bgm_asset is not None and bgm_asset.kind != "audio":
            self.warnings.append("背景音乐不是音频文件，已忽略")
            bgm_asset = None
        # 图片叠加引用的素材可能已被删掉，过滤掉它们，避免渲染时找不到文件。
        image_overlays = [
            (o, project.assets[o.image])
            for o in image_overlays
            if o.image in project.assets and project.assets[o.image].kind == "image"
        ]
        grade = grade_filter(project.look.grade)
        ambience = project.assets.get(project.sfx.ambience or "")
        if ambience is not None and ambience.kind != "audio":
            ambience = None
        whoosh = project.sfx.transitions and bool(cuts)
        if (
            not cues
            and bgm_asset is None
            and ambience is None
            and not whoosh
            and not text_overlays
            and not image_overlays
            and not project.ai_badge
            and not has_titles
            and project.loudness is None
            and not grade
            and not (self.quality == "final" and project.delivery != platforms.DEFAULT_PLATFORM)
        ):
            self.stages.append(StageRecord("final", "final", assembly_fp, True, 0.0))
            return assembly

        # 字幕和叠加文字挪进交付平台的安全区，不被平台界面挡住。
        style = style.model_copy(update={"margin": subtitle_margin(project)})
        text_overlays = [
            item.model_copy(update={"margin": safe_margin(project, item.position, item.margin)})
            for item in text_overlays
        ]
        margin_l, margin_r = side_margins(project)
        reencode = self.quality == "final" and project.delivery != platforms.DEFAULT_PLATFORM
        needs_ass = bool(cues or text_overlays or project.ai_badge or has_titles)
        faces = self._text_faces(project) if needs_ass else None
        font = faces["subtitle"].path if faces else None
        used_faces = sorted({face.path for face in faces.values()}) if faces else []
        fp = fingerprint(
            "final",
            assembly_fp,
            [asdict(c) for c in cues],
            style.model_dump() if cues else None,
            [o.model_dump() for o in text_overlays] if text_overlays else None,
            [(o.model_dump(), asset.sha256) for o, asset in image_overlays] if image_overlays else None,
            self.quality,
            project.ai_badge,
            [sha256_file(path) for path in used_faces] or None,
            bgm_asset.sha256 if bgm_asset else None,
            project.bgm.model_dump(exclude={"candidates", "query"}) if bgm_asset else None,
            project.loudness,
            AUDIO_MIX,
            (grade, GRADE_VERSION) if grade else None,
            project.delivery if project.delivery != platforms.DEFAULT_PLATFORM else None,
            (titles.model_dump(exclude={"candidates"}), shot_titles, project.title) if has_titles else None,
            (
                project.sfx.model_dump(),
                ambience.sha256 if ambience else None,
                cuts if whoosh else None,
                WHOOSH_VERSION,
            )
            if ambience or whoosh
            else None,
        )
        output = self.store.cache_dir(project.id, "final") / f"{fp}.mp4"
        info = probe(assembly)
        duration = info.duration or 0.0

        def build(temp: Path) -> None:
            with tempfile.TemporaryDirectory(prefix="shotwright-final-") as work_dir:
                work = Path(work_dir)
                args = ["-protocol_whitelist", LOCAL_PROTOCOLS, "-i", str(assembly)]
                filters: list[str] = []
                # 依次串起来的视频标签，每个叠加步骤接上一个。
                video_in, video_out = "0:v", "v0"
                step = 0

                if grade:
                    # 统一调色放在字幕和叠加文字之前，文字颜色不受影响。
                    filters.append(f"[{video_in}]{grade}[{video_out}]")
                    video_in = video_out
                    step += 1
                    video_out = f"v{step}"

                title_styles: list[str] = []
                title_events: list[str] = []
                if has_titles and faces is not None:
                    title_styles, title_events = subs.title_layers(
                        titles,
                        width=project.canvas.width,
                        height=project.canvas.height,
                        families={kind: faces[kind].ass_family for kind in ("headline", "intro", "shot")},
                        headline_margin=safe_margin(project, "top", titles.headline.margin),
                        shot_margin=shot_title_margin(project),
                        margin_l=margin_l,
                        margin_r=margin_r,
                        fallback_title=project.title,
                        shot_titles=shot_titles,
                        total=duration,
                    )
                if needs_ass and font is not None:
                    (work / "fonts").mkdir()
                    for path in used_faces:
                        shutil.copyfile(path, work / "fonts" / path.name)
                    family = faces["subtitle"].ass_family
                    (work / "subs.ass").write_text(
                        subs.to_ass(
                            cues,
                            style,
                            width=project.canvas.width,
                            height=project.canvas.height,
                            family=family,
                            overlays=text_overlays,
                            margin_l=margin_l,
                            margin_r=margin_r,
                            extra_styles=title_styles,
                            extra_events=title_events,
                        ),
                        encoding="utf-8",
                    )
                    filters.append(f"[{video_in}]ass=subs.ass:fontsdir=fonts[{video_out}]")
                    video_in = video_out
                    step += 1
                    video_out = f"v{step}"
                    if project.ai_badge:
                        # 角标：右下角一行小字，提示这是 AI 生成内容。
                        badge = "AI"
                        filters.append(
                            f"[{video_in}]drawtext=fontfile=fonts/{font.name}:text='{badge}':"
                            f"fontcolor=white@0.75:fontsize="
                            f"{max(16, project.canvas.height // 40)}:x=w-tw-24:y=h-th-24:"
                            f"box=1:boxcolor=black@0.35:boxborderw=8[{video_out}]"
                        )
                        video_in = video_out
                        step += 1
                        video_out = f"v{step}"

                # 图片叠加：按位置缩放到合适大小，在指定时间段内显示。
                for index, (item, asset) in enumerate(image_overlays, start=1):
                    args += [
                        "-protocol_whitelist",
                        LOCAL_PROTOCOLS,
                        "-i",
                        str(self.store.asset_path(project, asset.id)),
                    ]
                    height = max(24, item.size * 2)
                    label_in = f"img{index}"
                    filters.append(f"[{index}:v]scale=-2:{height}[{label_in}]")
                    x, y = _overlay_position(item, project)
                    enable = (
                        f":enable='between(t,{item.start:.2f},{_overlay_end(item):.2f})'"
                        if item.duration
                        else (f":enable='gte(t,{item.start:.2f})'")
                    )
                    filters.append(f"[{video_in}][{label_in}]overlay=x={x}:y={y}{enable}[{video_out}]")
                    video_in = video_out
                    step += 1
                    video_out = f"v{step}"

                audio_map = "0:a"
                bgm_index = None
                if bgm_asset is not None:
                    bgm_index = 1 + len(image_overlays)
                    args += [
                        "-stream_loop",
                        "-1",
                        "-protocol_whitelist",
                        LOCAL_PROTOCOLS,
                        "-i",
                        str(self.store.asset_path(project, bgm_asset.id)),
                    ]
                ambience_index = whoosh_index = None
                next_index = 1 + len(image_overlays) + (1 if bgm_asset is not None else 0)
                if ambience is not None:
                    ambience_index = next_index
                    next_index += 1
                    args += [
                        "-stream_loop",
                        "-1",
                        "-protocol_whitelist",
                        LOCAL_PROTOCOLS,
                        "-i",
                        str(self.store.asset_path(project, ambience.id)),
                    ]
                if whoosh:
                    whoosh_index = next_index
                    args += ["-protocol_whitelist", LOCAL_PROTOCOLS, "-i", str(self._whoosh(project))]
                audio_graph = audio_filter(
                    project,
                    bgm_index,
                    duration,
                    ambience_index=ambience_index,
                    whoosh_index=whoosh_index,
                    cuts=cuts if whoosh else (),
                )
                if audio_graph:
                    filters.append(audio_graph)
                    audio_map = "[a]"
                if reencode and not video_in.startswith("v"):
                    # 交付平台有码率和编码要求，没有滤镜时也要重新编码一次。
                    filters.append(f"[{video_in}]null[{video_out}]")
                    video_in = video_out
                command = [*args]
                if filters:
                    command += ["-filter_complex", ";".join(filters)]
                # 视频没经过滤镜时直接引用输入流（0:v），不能写成 [0:v]——
                # 方括号形式只在滤镜图里有同名的输出标签时才成立。
                video_map = f"[{video_in}]" if video_in.startswith("v") else video_in
                command += ["-map", video_map, "-map", audio_map]
                if not video_in.startswith("v"):
                    command += ["-c:v", "copy"]
                elif reencode:
                    command += platforms.encode_args(project.delivery, project.canvas.fps)
                else:
                    command += _video_args(project.canvas.fps, self.quality)
                command += AUDIO_ARGS if audio_map != "0:a" else ["-c:a", "copy"]
                mixed = work / "mixed.mp4"
                command += [
                    "-t",
                    f"{duration:.3f}",
                    "-movflags",
                    "+faststart",
                    "-f",
                    "mp4",
                    str(mixed.resolve()),
                ]
                if filters:
                    run_ffmpeg(command, cwd=work, cancel=self._cancel)
                else:
                    shutil.copyfile(assembly, mixed)
                self._normalize(project, mixed, temp)

        try:
            self._stage("final", "final", fp, output, build)
        except MediaCancelled as exc:
            raise RenderCancelled(str(exc)) from exc
        except MediaError as exc:
            raise RenderError(f"合成字幕与背景音乐失败：{exc}") from exc
        return output


def audio_filter(
    project: Project,
    bgm_index: int | None,
    duration: float,
    *,
    ambience_index: int | None = None,
    whoosh_index: int | None = None,
    cuts: list[float] | tuple[float, ...] = (),
) -> str:
    """配音之外的声音层：背景音乐（闪避、淡入淡出）、环境声、转场音效。输出标签为 ``[a]``。

    * 闪避：配音（含原声）作为侧链压缩背景音乐，说话时音乐自动压低，停顿时恢复；
    * 环境声：循环铺满整片，音量很低，开头结尾淡入淡出；
    * 转场音效：在每个切点前后放一声轻的「嗖」。

    什么都没有时返回空字符串，音频直接复制。响度统一在混音之后单独做（见 :meth:`Renderer._normalize`）。
    """
    parts: list[str] = []
    main = "[0:a]"
    layers: list[str] = []
    if bgm_index is not None:
        bgm = project.bgm
        if bgm.duck:
            parts.append("[0:a]asplit=2[main][side]")
            main = "[main]"
        chain = f"[{bgm_index}:a]aresample=48000,aformat=channel_layouts=stereo,volume={bgm.volume:.3f}"
        if bgm.fade_in > 0:
            chain += f",afade=t=in:st=0:d={bgm.fade_in:.2f}"
        if bgm.fade_out > 0:
            fade = min(bgm.fade_out, duration)
            chain += f",afade=t=out:st={max(duration - fade, 0.0):.3f}:d={fade:.2f}"
        if bgm.duck:
            parts.append(chain + "[bgm]")
            # 说话时把音乐再压低约 10 dB；释放时间长一些，句子之间不会忽高忽低。
            parts.append("[bgm][side]sidechaincompress=threshold=0.02:ratio=8:attack=30:release=600[b]")
        else:
            parts.append(chain + "[b]")
        layers.append("[b]")
    if ambience_index is not None:
        fade = min(1.5, duration / 3)
        parts.append(
            f"[{ambience_index}:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"volume={project.sfx.ambience_volume:.3f},afade=t=in:st=0:d={fade:.2f},"
            f"afade=t=out:st={max(duration - fade, 0.0):.3f}:d={fade:.2f}[amb]"
        )
        layers.append("[amb]")
    points = [cut for cut in cuts if 0.3 < cut < duration - 0.2]
    if whoosh_index is not None and points:
        source = f"[{whoosh_index}:a]aresample=48000,aformat=channel_layouts=stereo,volume={project.sfx.transition_volume:.3f}"
        if len(points) == 1:
            parts.append(f"{source},adelay={_delay_ms(points[0])}:all=1[wh]")
        else:
            labels = "".join(f"[w{i}]" for i in range(len(points)))
            parts.append(f"{source},asplit={len(points)}{labels}")
            parts += [f"[w{i}]adelay={_delay_ms(cut)}:all=1[wd{i}]" for i, cut in enumerate(points)]
            delayed = "".join(f"[wd{i}]" for i in range(len(points)))
            parts.append(f"{delayed}amix=inputs={len(points)}:normalize=0[wh]")
        layers.append("[wh]")
    if not layers:
        return ""
    parts.append(f"{main}{''.join(layers)}amix=inputs={len(layers) + 1}:duration=first:normalize=0[a]")
    return ";".join(parts)


WHOOSH_VERSION = "whoosh-1"
"""转场音效的版本；改动合成参数时同步修改，让缓存失效。"""
WHOOSH_LEAD = 0.25
"""转场音效比切点提前多少秒开始（音效的峰值正好落在切点附近）。"""


def _delay_ms(cut: float) -> int:
    return max(0, int(round((cut - WHOOSH_LEAD) * 1000)))
