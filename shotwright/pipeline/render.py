"""镜头级增量渲染。

阶段与依赖（详见 docs/ARCHITECTURE.md）：

    每个镜头：画面准备 → 配音 → 镜头渲染（含时长计算）
    整片：    拼接 → 字幕与背景音乐 → 导出

每个阶段的产出按指纹缓存在 ``cache/<阶段>/<指纹>.*``。重新渲染时，输入没变的阶段
直接复用缓存：只改了一个镜头的文案，就只会重做这个镜头的配音和渲染，再加上整片的
拼接与导出。增量渲染的思路参考了 MoneyPrinterTurbo 的 ``video_project.py``。
"""

from __future__ import annotations

import math
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from shotwright.config import Settings
from shotwright.core.fingerprint import fingerprint
from shotwright.core.ids import new_id
from shotwright.core.models import Project, Shot
from shotwright.core.store import ProjectStore, read_json, sha256_file, write_json
from shotwright.media import subtitles as subs
from shotwright.media.ffmpeg import LOCAL_PROTOCOLS, MediaError, probe, run_ffmpeg
from shotwright.providers import registry
from shotwright.providers.base import ProviderError, TTSProvider
from shotwright.services import visuals

Progress = Callable[[float, str], None]

VOICE_PADDING = 0.25
"""配音后留的空隙（秒），让镜头之间有呼吸感。"""


KEN_BURNS = "ken-burns-1"
"""图片镜头的运动效果版本；改动滤镜参数时同步修改，让旧缓存失效。"""


def _fit_filter(width: int, height: int, fit: str) -> str:
    if fit == "contain":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"


def ken_burns_filter(width: int, height: int, fps: int, duration: float, fit: str) -> str:
    """图片慢推：先按两倍画布适配（减少缩放抖动），再从 1.0 匀速放大到 1.1 倍。"""
    frames = math.ceil(duration * fps) + 1
    return (
        f"{_fit_filter(width * 2, height * 2, fit)},"
        f"zoompan=z='1+0.1*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={width}x{height}:fps={fps},setsar=1,format=yuv420p"
    )


class RenderError(RuntimeError):
    pass


class RenderCancelled(RenderError):
    pass


@dataclass
class StageRecord:
    key: str
    kind: str
    fingerprint: str
    reused: bool
    seconds: float


@dataclass
class ShotTiming:
    shot_id: str
    start: float
    voice_duration: float
    duration: float


@dataclass
class RenderResult:
    render_id: str
    project_id: str
    revision: int
    video: str
    subtitles: str | None
    duration: float
    timings: list[ShotTiming]
    stages: list[StageRecord]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _video_args(fps: int) -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-g",
        str(fps * 2),
    ]


AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


class Renderer:
    def __init__(
        self,
        store: ProjectStore,
        settings: Settings,
        *,
        progress: Progress | None = None,
        cancel: threading.Event | None = None,
        allow_paid: bool = False,
    ):
        self.store = store
        self.allow_paid = allow_paid
        """用户是否已确认本次渲染可以调用收费的 AI 生成。"""
        self.settings = settings
        self._progress = progress or (lambda value, message: None)
        self._cancel = cancel or threading.Event()
        self.stages: list[StageRecord] = []
        self.warnings: list[str] = []

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
        voices: list[tuple[str, Path, float]] = []
        for index, shot in enumerate(project.shots):
            self._report(0.10 + 0.25 * index / total, f"配音 {index + 1}/{total}")
            voices.append(self._voice(project, shot))

        shot_files: list[tuple[str, Path]] = []
        timings: list[ShotTiming] = []
        cursor = 0.0
        for index, (shot, (voice_fp, voice_path, voice_duration)) in enumerate(
            zip(project.shots, voices, strict=True)
        ):
            self._report(0.35 + 0.45 * index / total, f"渲染镜头 {index + 1}/{total}")
            duration = round(shot.duration or voice_duration + VOICE_PADDING, 3)
            fp, path = self._shot(project, shot, voice_fp, voice_path, duration)
            shot_files.append((fp, path))
            timings.append(ShotTiming(shot.id, round(cursor, 3), voice_duration, duration))
            cursor += duration

        self._report(0.80, "拼接镜头")
        assembly_fp, assembly = self._assemble(project, shot_files)

        self._report(0.85, "合成字幕与背景音乐")
        cues = self._cues(project, timings)
        final = self._final(project, assembly_fp, assembly, cues)

        output_dir = self.store.render_dir(project.id, render_id)
        video_path = output_dir / "video.mp4"
        shutil.copyfile(final, video_path)
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
                    # AI 画面没生成时，有检索词就退回素材检索，而不是直接用纯色。
                    if visual.mode == "stock" or not visual.terms:
                        raise
                    visuals.fetch_stock_candidates(self.store, self.settings, project, shot)
                    self.warnings.append(f"镜头 {index + 1} 的 AI 画面没有生成（{exc}），已改用素材检索")
                changed[shot.id] = (list(visual.candidates), visual.selected)
            except ProviderError as exc:
                self.warnings.append(f"镜头 {index + 1} 没有拿到素材，已使用纯色画面：{exc}")
        if not changed:
            return
        # 渲染期间用户可能在编辑：只把本次新增的素材合并进最新的项目文件。
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

    # ---- 配音 ---------------------------------------------------------------

    def _voice(self, project: Project, shot: Shot) -> tuple[str, Path, float]:
        voice = project.voice_for(shot)
        text = shot.text.strip()
        provider_id = voice.provider if text else "silence"
        fp = fingerprint("voice", text, voice.model_dump(), shot.duration if not text else None)
        cache = self.store.cache_dir(project.id, "voice")
        meta_path = cache / f"{fp}.json"
        meta = read_json(meta_path)
        if meta and (cache / meta.get("file", "")).is_file():
            self.stages.append(StageRecord(f"{shot.id}:voice", "voice", fp, True, 0.0))
            return fp, cache / meta["file"], float(meta["duration"])

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="shotwright-voice-") as temp:
            if not text:
                seconds = shot.duration or 2.0
                path = Path(temp) / "voice.m4a"
                run_ffmpeg(
                    [
                        "-f",
                        "lavfi",
                        "-i",
                        "anullsrc=channel_layout=stereo:sample_rate=48000",
                        "-t",
                        f"{seconds:.3f}",
                        *AUDIO_ARGS,
                        str(path),
                    ]
                )
                duration = seconds
            else:
                try:
                    tts: TTSProvider = registry.create("tts", provider_id, self.settings)  # type: ignore[assignment]
                    speech = tts.synthesize(
                        text,
                        voice=voice.voice,
                        rate=voice.rate,
                        volume=voice.volume,
                        output=Path(temp) / "voice",
                    )
                except ProviderError as exc:
                    index = project.shots.index(shot) + 1
                    raise RenderError(f"镜头 {index} 配音失败：{exc}") from exc
                path, duration = speech.path, speech.duration
            filename = f"{fp}{path.suffix}"
            shutil.move(str(path), cache / filename)
        write_json(meta_path, {"file": filename, "duration": round(duration, 3), "provider": provider_id})
        self.stages.append(
            StageRecord(f"{shot.id}:voice", "voice", fp, False, round(time.monotonic() - started, 3))
        )
        return fp, cache / filename, round(duration, 3)

    # ---- 镜头 ---------------------------------------------------------------

    def _shot(
        self, project: Project, shot: Shot, voice_fp: str, voice_path: Path, duration: float
    ) -> tuple[str, Path]:
        canvas = project.canvas
        visual = shot.visual
        asset = project.assets.get(visual.selected) if visual.selected else None
        if asset is not None and asset.kind not in ("video", "image"):
            asset = None
        visual_key = asset.sha256 if asset else f"color:{visual.color}"
        fp = fingerprint(
            "shot",
            voice_fp,
            duration,
            visual_key,
            visual.fit,
            KEN_BURNS if asset is not None and asset.kind == "image" else None,
            canvas.model_dump(),
            project.voice_for(shot).volume,
        )
        output = self.store.cache_dir(project.id, "shots") / f"{fp}.mp4"
        width, height, fps = canvas.width, canvas.height, canvas.fps

        def build(temp: Path) -> None:
            if asset is None:
                video_input = [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=0x{visual.color[1:]}:s={width}x{height}:r={fps}",
                ]
            elif asset.kind == "image":
                # 单帧输入，由 zoompan 展开成整段慢推镜头。
                video_input = [
                    "-protocol_whitelist",
                    LOCAL_PROTOCOLS,
                    "-i",
                    str(self.store.asset_path(project, asset.id)),
                ]
            else:
                video_input = [
                    "-stream_loop",
                    "-1",
                    "-protocol_whitelist",
                    LOCAL_PROTOCOLS,
                    "-i",
                    str(self.store.asset_path(project, asset.id)),
                ]
            if asset is not None and asset.kind == "image":
                video_chain = ken_burns_filter(width, height, fps, duration, visual.fit)
            else:
                video_chain = f"{_fit_filter(width, height, visual.fit)},setsar=1,fps={fps},format=yuv420p"
            graph = f"[0:v]{video_chain}[v];[1:a]aresample=48000,aformat=channel_layouts=stereo,apad[a]"
            run_ffmpeg(
                [
                    *video_input,
                    "-protocol_whitelist",
                    LOCAL_PROTOCOLS,
                    "-i",
                    str(voice_path),
                    "-filter_complex",
                    graph,
                    "-map",
                    "[v]",
                    "-map",
                    "[a]",
                    "-t",
                    f"{duration:.3f}",
                    *_video_args(fps),
                    *AUDIO_ARGS,
                    "-movflags",
                    "+faststart",
                    "-f",
                    "mp4",
                    str(temp),
                ]
            )

        self._stage(f"{shot.id}:shot", "shot", fp, output, build)
        return fp, output

    # ---- 整片 ---------------------------------------------------------------

    def _assemble(self, project: Project, shots: list[tuple[str, Path]]) -> tuple[str, Path]:
        fp = fingerprint("assembly", [item[0] for item in shots])
        output = self.store.cache_dir(project.id, "assembly") / f"{fp}.mp4"

        def build(temp: Path) -> None:
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
                    ]
                )
            finally:
                listing.unlink(missing_ok=True)

        self._stage("assembly", "assembly", fp, output, build)
        return fp, output

    def _font_path(self, project: Project) -> Path:
        path = (self.settings.fonts_dir / project.subtitle.font).resolve()
        if path.parent != self.settings.fonts_dir.resolve() or not path.is_file():
            raise RenderError(f"找不到字幕字体：{project.subtitle.font}")
        return path

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
                )
            )
        return cues

    def _final(self, project: Project, assembly_fp: str, assembly: Path, cues: list[subs.Cue]) -> Path:
        style = project.subtitle
        bgm_asset = project.assets.get(project.bgm.asset_id) if project.bgm.asset_id else None
        if bgm_asset is not None and bgm_asset.kind != "audio":
            self.warnings.append("背景音乐不是音频文件，已忽略")
            bgm_asset = None
        if not cues and bgm_asset is None:
            self.stages.append(StageRecord("final", "final", assembly_fp, True, 0.0))
            return assembly

        font = self._font_path(project) if cues else None
        fp = fingerprint(
            "final",
            assembly_fp,
            [asdict(c) for c in cues],
            style.model_dump() if cues else None,
            sha256_file(font) if font else None,
            bgm_asset.sha256 if bgm_asset else None,
            project.bgm.volume if bgm_asset else None,
        )
        output = self.store.cache_dir(project.id, "final") / f"{fp}.mp4"
        info = probe(assembly)
        duration = info.duration or 0.0

        def build(temp: Path) -> None:
            with tempfile.TemporaryDirectory(prefix="shotwright-final-") as work_dir:
                work = Path(work_dir)
                args = ["-protocol_whitelist", LOCAL_PROTOCOLS, "-i", str(assembly)]
                filters = []
                video_map, audio_map = "0:v", "0:a"
                if cues and font is not None:
                    # 字幕文件和字体都放进工作目录、用相对路径引用，
                    # 避免 Windows 盘符在滤镜参数里的转义问题。
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
                        ),
                        encoding="utf-8",
                    )
                    filters.append("[0:v]ass=subs.ass:fontsdir=fonts[v]")
                    video_map = "[v]"
                if bgm_asset is not None:
                    args += [
                        "-stream_loop",
                        "-1",
                        "-protocol_whitelist",
                        LOCAL_PROTOCOLS,
                        "-i",
                        str(self.store.asset_path(project, bgm_asset.id)),
                    ]
                    fade_start = max(duration - 2.0, 0.0)
                    filters.append(
                        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,"
                        f"volume={project.bgm.volume:.3f},afade=t=out:st={fade_start:.3f}:d=2[b];"
                        f"[0:a][b]amix=inputs=2:duration=first:normalize=0[a]"
                    )
                    audio_map = "[a]"
                command = [*args]
                if filters:
                    command += ["-filter_complex", ";".join(filters)]
                command += ["-map", video_map, "-map", audio_map]
                command += _video_args(project.canvas.fps) if video_map != "0:v" else ["-c:v", "copy"]
                command += AUDIO_ARGS if audio_map != "0:a" else ["-c:a", "copy"]
                command += [
                    "-t",
                    f"{duration:.3f}",
                    "-movflags",
                    "+faststart",
                    "-f",
                    "mp4",
                    str(temp.resolve()),
                ]
                run_ffmpeg(command, cwd=work)

        try:
            self._stage("final", "final", fp, output, build)
        except MediaError as exc:
            raise RenderError(f"合成字幕与背景音乐失败：{exc}") from exc
        return output
