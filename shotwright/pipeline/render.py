"""镜头级增量渲染。

阶段与依赖（详见 docs/ARCHITECTURE.md）：

    每个镜头：画面准备 → 配音 → 镜头渲染（含时长计算）
    整片：    拼接 → 字幕与背景音乐 → 导出

每个阶段的产出按指纹缓存在 ``cache/<阶段>/<指纹>.*``。重新渲染时，输入没变的阶段
直接复用缓存：只改了一个镜头的文案，就只会重做这个镜头的配音和渲染，再加上整片的
拼接与导出。增量渲染的思路参考了 MoneyPrinterTurbo 的 ``video_project.py``。
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from shotwright.config import Settings
from shotwright.core.fingerprint import fingerprint
from shotwright.core.ids import new_id
from shotwright.core.models import Project, Shot
from shotwright.core.store import ProjectStore, sha256_file, write_json
from shotwright.media import subtitles as subs
from shotwright.media.ffmpeg import (
    LOCAL_PROTOCOLS,
    MediaCancelled,
    MediaError,
    loudnorm_stats,
    probe,
    run_ffmpeg,
)
from shotwright.providers.base import ProviderError
from shotwright.services import visuals

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

Progress = Callable[[float, str], None]

Quality = Literal["draft", "final"]


def _overlay_end(item) -> float:
    """叠加图片的结束时间；不限时长时给一个足够大的数。"""
    return item.start + item.duration if item.duration else 1e6


def _overlay_position(item, project: Project) -> tuple[str, str]:
    """图片叠加的位置表达式（ffmpeg overlay 的 x / y）。"""
    margin = item.margin
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
        self._font: Path | None = None

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
        final = self._final(project, assembly_fp, assembly, cues)

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

    # ---- 画面 ---------------------------------------------------------------

    def _prepare_visuals(self, project: Project) -> None:
        """为还没有画面的镜头自动检索或生成一次素材。

        AI 镜头只有在 ``allow_paid`` 为真时才会提交收费任务；未确认或生成失败时，
        有检索词就改用素材检索，仍然拿不到才退回纯色画面。每种情况都记录提示。
        """
        if not self.settings.auto_fetch_visuals:
            return
        changed: dict[str, tuple[list[str], str | None]] = {}
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
                        self.store, self.settings, project, shot, confirm_charge=self.allow_paid
                    )
                except ProviderError as exc:
                    if visual.mode == "stock" or not visual.terms:
                        raise
                    visuals.fetch_stock_candidates(self.store, self.settings, project, shot)
                    self.warnings.append(f"镜头 {index + 1} 的 AI 画面没有生成（{exc}），已改用素材检索")
                changed[shot.id] = (list(visual.candidates), visual.selected)
            except ProviderError as exc:
                self.warnings.append(f"镜头 {index + 1} 没有拿到素材，已使用纯色画面：{exc}")
        if not changed:
            return
        with self.store.lock(project.id):
            latest = self.store.load(project.id)
            for asset_id, asset in project.assets.items():
                latest.assets.setdefault(asset_id, asset)
            for shot_id, (candidates, selected) in changed.items():
                try:
                    target = latest.shot(shot_id)
                except KeyError:
                    continue
                if target.visual.selected is None:
                    target.visual.candidates = candidates
                    target.visual.selected = selected
            self.store.save(latest)
        project.revision = latest.revision

    def _has_visual(self, project: Project, shot: Shot) -> bool:
        selected = shot.visual.selected
        return bool(
            selected and selected in project.assets and project.assets[selected].kind in ("video", "image")
        )

    # ---- 整片 ---------------------------------------------------------------

    def _assemble(
        self, project: Project, shots: list[tuple[str, Path]], overlap: float
    ) -> tuple[str, Path]:
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
                    "-f", "concat",
                    "-safe", "0",
                    "-protocol_whitelist", LOCAL_PROTOCOLS,
                    "-i", str(listing),
                    "-c", "copy",
                    "-movflags", "+faststart",
                    "-f", "mp4",
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
                f"[{index}:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"adelay={delay}:all=1[a{index}]"
            )
        mix_inputs = "".join(f"[a{index}]" for index in range(len(shots)))
        audio_chain.append(
            f"{mix_inputs}amix=inputs={len(shots)}:duration=longest:normalize=0[aout]"
        )
        run_ffmpeg(
            [
                *args,
                "-filter_complex", ";".join([*chain, *audio_chain]),
                "-map", f"[{previous}]",
                "-map", "[aout]",
                *_video_args(project.canvas.fps, self.quality),
                *AUDIO_ARGS,
                "-movflags", "+faststart",
                "-f", "mp4",
                str(temp),
            ],
            cancel=self._cancel,
        )

    def _font_path(self, project: Project) -> Path:
        """字幕字体；缺字时（例如中文字体显示韩文）自动换成能显示的内置字体并提示。"""
        if self._font is not None:
            return self._font
        fonts_dir = self.settings.fonts_dir.resolve()
        path = (fonts_dir / project.subtitle.font).resolve()
        if path.parent != fonts_dir or not path.is_file():
            raise RenderError(f"找不到字幕字体：{project.subtitle.font}")
        text = "".join(shot.text for shot in project.shots)
        chosen, missing = subs.pick_font(path, fonts_dir, text)
        if chosen != path:
            self.warnings.append(f"字幕字体 {path.name} 缺少部分文字，已改用 {chosen.name}")
        if missing:
            self.warnings.append(
                f"字幕里有字体无法显示的文字：{missing[:20]}，请在 resources/fonts 中加入支持该语言的字体"
            )
        self._font = chosen
        return chosen

    def _cues(self, project: Project, timings: list[ShotTiming]) -> list[subs.Cue]:
        style = project.subtitle
        if not style.enabled:
            return []
        font = self._font_path(project)
        max_width = int(project.canvas.width * 0.86)
        cues: list[subs.Cue] = []
        for shot, timing in zip(project.shots, timings, strict=True):
            if not shot.text.strip():
                continue
            spoken = min(timing.voice_duration, timing.duration)
            cues.extend(
                subs.split_cues(
                    shot.text,
                    timing.start,
                    timing.start + spoken,
                    font_path=font,
                    size=style.size,
                    max_width=max_width,
                    words=timing.words,
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
        measured = ":".join(f"measured_{key[6:]}={value}" for key, value in stats.items() if key.startswith("input_"))
        audio = (
            f"loudnorm=I={project.loudness:g}:TP={TRUE_PEAK:g}:LRA=11:{measured}:"
            f"offset={stats['target_offset']}:linear=true,aresample=48000"
        )
        run_ffmpeg(
            [
                "-protocol_whitelist", LOCAL_PROTOCOLS,
                "-i", str(source),
                "-map", "0:v", "-map", "0:a",
                "-c:v", "copy",
                "-af", audio,
                *AUDIO_ARGS,
                "-movflags", "+faststart",
                "-f", "mp4",
                str(output.resolve()),
            ],
            cancel=self._cancel,
        )

    def _final(self, project: Project, assembly_fp: str, assembly: Path, cues: list[subs.Cue]) -> Path:
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
        if (
            not cues
            and bgm_asset is None
            and not text_overlays
            and not image_overlays
            and not project.ai_badge
            and project.loudness is None
        ):
            self.stages.append(StageRecord("final", "final", assembly_fp, True, 0.0))
            return assembly

        needs_ass = bool(cues or text_overlays or project.ai_badge)
        font = self._font_path(project) if needs_ass else None
        fp = fingerprint(
            "final",
            assembly_fp,
            [asdict(c) for c in cues],
            style.model_dump() if cues else None,
            [o.model_dump() for o in text_overlays] if text_overlays else None,
            [(o.model_dump(), asset.sha256) for o, asset in image_overlays] if image_overlays else None,
            self.quality,
            project.ai_badge,
            sha256_file(font) if font else None,
            bgm_asset.sha256 if bgm_asset else None,
            project.bgm.model_dump(exclude={"candidates", "query"}) if bgm_asset else None,
            project.loudness,
            AUDIO_MIX,
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

                if needs_ass and font is not None:
                    (work / "fonts").mkdir()
                    shutil.copyfile(font, work / "fonts" / font.name)
                    family = subs.font_family(font)
                    (work / "subs.ass").write_text(
                        subs.to_ass(
                            cues,
                            style,
                            width=project.canvas.width,
                            height=project.canvas.height,
                            family=family,
                            overlays=text_overlays,
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
                        "-protocol_whitelist", LOCAL_PROTOCOLS,
                        "-i", str(self.store.asset_path(project, asset.id)),
                    ]
                    height = max(24, item.size * 2)
                    label_in = f"img{index}"
                    filters.append(f"[{index}:v]scale=-2:{height}[{label_in}]")
                    x, y = _overlay_position(item, project)
                    enable = f":enable='between(t,{item.start:.2f},{_overlay_end(item):.2f})'" if item.duration else (
                        f":enable='gte(t,{item.start:.2f})'"
                    )
                    filters.append(
                        f"[{video_in}][{label_in}]overlay=x={x}:y={y}{enable}[{video_out}]"
                    )
                    video_in = video_out
                    step += 1
                    video_out = f"v{step}"

                audio_map = "0:a"
                bgm_index = None
                if bgm_asset is not None:
                    bgm_index = 1 + len(image_overlays)
                    args += [
                        "-stream_loop", "-1",
                        "-protocol_whitelist", LOCAL_PROTOCOLS,
                        "-i", str(self.store.asset_path(project, bgm_asset.id)),
                    ]
                audio_graph = audio_filter(project, bgm_index, duration)
                if audio_graph:
                    filters.append(audio_graph)
                    audio_map = "[a]"
                command = [*args]
                if filters:
                    command += ["-filter_complex", ";".join(filters)]
                # 视频没经过滤镜时直接引用输入流（0:v），不能写成 [0:v]——
                # 方括号形式只在滤镜图里有同名的输出标签时才成立。
                video_map = f"[{video_in}]" if video_in.startswith("v") else video_in
                command += ["-map", video_map, "-map", audio_map]
                command += _video_args(project.canvas.fps, self.quality) if video_in.startswith("v") else ["-c:v", "copy"]
                command += AUDIO_ARGS if audio_map != "0:a" else ["-c:a", "copy"]
                mixed = work / "mixed.mp4"
                command += [
                    "-t", f"{duration:.3f}",
                    "-movflags", "+faststart",
                    "-f", "mp4",
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


def audio_filter(project: Project, bgm_index: int | None, duration: float) -> str:
    """背景音乐的滤镜图：闪避与淡入淡出，输出标签为 ``[a]``；没有背景音乐时返回空字符串。

    闪避：配音（含原声）作为侧链压缩背景音乐，说话时音乐自动压低，停顿时恢复。
    响度统一在混音之后单独做（见 :meth:`Renderer._normalize`）。
    """
    parts: list[str] = []
    current = "[0:a]"
    if bgm_index is not None:
        bgm = project.bgm
        main = "[0:a]"
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
        parts.append(f"{main}[b]amix=inputs=2:duration=first:normalize=0[mix]")
        current = "[mix]"
    if current != "[0:a]":
        parts.append(f"{current}anull[a]")
    return ";".join(parts)
