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

from shotwright.config import Settings
from shotwright.core.fingerprint import fingerprint
from shotwright.core.ids import new_id
from shotwright.core.models import Project, Shot
from shotwright.core.store import ProjectStore, sha256_file, write_json
from shotwright.media import subtitles as subs
from shotwright.media.ffmpeg import LOCAL_PROTOCOLS, MediaCancelled, MediaError, probe, run_ffmpeg
from shotwright.providers.base import ProviderError
from shotwright.services import visuals

from .render_types import (
    AUDIO_ARGS,
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

        shot_files: list[tuple[str, Path]] = []
        timings: list[ShotTiming] = []
        cursor = 0.0
        for index, (shot, (voice_fp, voice_path, voice_duration, words)) in enumerate(
            zip(project.shots, voices, strict=True)
        ):
            self._report(0.35 + 0.45 * index / total, f"渲染镜头 {index + 1}/{total}")
            duration = round(shot.duration or voice_duration + VOICE_PADDING, 3)
            fp, path = render_shot(
                self.store,
                project,
                shot,
                voice_fp,
                voice_path,
                duration,
                stages=self.stages,
                cancel=self._cancel,
            )
            shot_files.append((fp, path))
            timings.append(ShotTiming(shot.id, round(cursor, 3), voice_duration, duration, words))
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
                    ],
                    cancel=self._cancel,
                )
            finally:
                listing.unlink(missing_ok=True)

        self._stage("assembly", "assembly", fp, output, build)
        return fp, output

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
                run_ffmpeg(command, cwd=work, cancel=self._cancel)

        try:
            self._stage("final", "final", fp, output, build)
        except MediaCancelled as exc:
            raise RenderCancelled(str(exc)) from exc
        except MediaError as exc:
            raise RenderError(f"合成字幕与背景音乐失败：{exc}") from exc
        return output
