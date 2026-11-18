"""本地渲染基准：不联网、不花钱，只测渲染引擎有多快。

和 :mod:`shotwright.services.bench` 的分工：

- ``bench``：测「从诉求到分镜」的质量，需要大模型，跑的是提示词工程；
- ``bench_local``：测「改一个镜头只重渲这个镜头」到底能省多少，用示例项目 + 静音配音，
  任何一台装了 FFmpeg 的机器都能复现。

后者是 Shotwright 的结构性优势（镜头级指纹缓存），也是最容易被验证、被转发的数字。
报告里的四个数：冷启动整片耗时、改一个镜头后的重渲耗时（中位数）、缓存复用率、峰值内存。

一条命令：

    shotwright bench --local

结果写到 ``storage/bench/local-<时间>/``：``report.json``、``report.md``、``sample.mp4``。
"""

from __future__ import annotations

import shutil
import statistics
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from shotwright.config import Settings
from shotwright.core import sysinfo
from shotwright.core.store import ProjectStore, write_json
from shotwright.media import ffmpeg
from shotwright.pipeline.render import Renderer
from shotwright.pipeline.render_types import RenderResult
from shotwright.services import sample

Quality = Literal["draft", "final"]

EDIT_PREFIX = "【基准改动】"
"""改动文案的前缀：一眼能看出这是基准项目，不会和真实项目混淆。"""


@dataclass
class RunResult:
    """一次渲染的耗时和缓存情况。"""

    label: str
    seconds: float
    stages: int
    reused: int
    redone: list[str] = field(default_factory=list)
    """这一轮重新做过的阶段类别（voice / shot / final 等）。"""
    shots_rendered: int = 0
    """这一轮真正重渲的镜头数（冷启动时等于总镜头数）。"""
    stage_seconds: dict[str, float] = field(default_factory=dict)
    """这一轮各阶段真正花掉的时间（只统计重新做的阶段，复用的记 0）。"""
    duration: float = 0.0
    """成片时长（秒）。"""

    @property
    def reuse_ratio(self) -> float:
        return round(self.reused / self.stages, 3) if self.stages else 0.0

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "seconds": round(self.seconds, 3),
            "stages": self.stages,
            "reused": self.reused,
            "redone": self.redone,
            "reuse_ratio": self.reuse_ratio,
            "shots_rendered": self.shots_rendered,
            "stage_seconds": self.stage_seconds,
            "duration": round(self.duration, 3),
        }


@dataclass
class LocalBenchReport:
    started_at: float
    machine: dict
    ffmpeg: str
    quality: str
    language: str
    shots: int
    build_seconds: float
    cold_runs: list[RunResult]
    warm: list[RunResult]
    peak_rss_mb: float | None
    video: str
    video_bytes: int

    @property
    def cold(self) -> RunResult:
        """冷启动的代表值：取耗时中位数的那一次（只跑一次时就是它本身）。"""
        if len(self.cold_runs) == 1:
            return self.cold_runs[0]
        middle = statistics.median(run.seconds for run in self.cold_runs)
        return min(self.cold_runs, key=lambda run: abs(run.seconds - middle))

    @property
    def median_cold(self) -> float:
        if not self.cold_runs:
            return 0.0
        return round(statistics.median(run.seconds for run in self.cold_runs), 3)

    @property
    def cold_spread_pct(self) -> float:
        """冷启动的波动幅度：(最大 − 最小) ÷ 中位数；只跑一次时为 0。"""
        if len(self.cold_runs) < 2 or self.median_cold <= 0:
            return 0.0
        seconds = [run.seconds for run in self.cold_runs]
        return round((max(seconds) - min(seconds)) / self.median_cold * 100, 1)

    @property
    def median_warm(self) -> float:
        return round(statistics.median(run.seconds for run in self.warm), 3) if self.warm else 0.0

    @property
    def speedup(self) -> float:
        """整片冷启动耗时是「改一个镜头」的几倍。"""
        return round(self.median_cold / self.median_warm, 1) if self.median_warm > 0 else 0.0

    def summary(self) -> dict:
        return {
            "cold_seconds": self.median_cold,
            "cold_min": round(min(run.seconds for run in self.cold_runs), 3) if self.cold_runs else 0.0,
            "cold_max": round(max(run.seconds for run in self.cold_runs), 3) if self.cold_runs else 0.0,
            "cold_spread_pct": self.cold_spread_pct,
            "warm_seconds": self.median_warm,
            "speedup": self.speedup,
            "warm_reuse_ratio": round(statistics.median(run.reuse_ratio for run in self.warm), 3)
            if self.warm
            else 0.0,
            "warm_shots_rendered": round(statistics.median(run.shots_rendered for run in self.warm))
            if self.warm
            else 0,
            "warm_stage_seconds": _median_stage_seconds(self.warm),
            "peak_rss_mb": self.peak_rss_mb,
            "shots": self.shots,
            "video_seconds": round(self.cold.duration, 3),
        }

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "machine": self.machine,
            "ffmpeg": self.ffmpeg,
            "quality": self.quality,
            "language": self.language,
            "shots": self.shots,
            "build_seconds": round(self.build_seconds, 3),
            "summary": self.summary(),
            "cold": self.cold.to_dict(),
            "cold_runs": [run.to_dict() for run in self.cold_runs],
            "warm": [run.to_dict() for run in self.warm],
            "video": self.video,
            "video_bytes": self.video_bytes,
        }


def _median_stage_seconds(runs: list[RunResult]) -> dict[str, float]:
    """各轮「改一个镜头」里，每个阶段花掉时间的中位数（按耗时从大到小排）。"""
    kinds = {kind for run in runs for kind in run.stage_seconds}
    values = {
        kind: round(statistics.median(run.stage_seconds.get(kind, 0.0) for run in runs), 2) for kind in kinds
    }
    return dict(sorted(values.items(), key=lambda item: -item[1]))


def _offline_settings(settings: Settings) -> Settings:
    """基准专用的配置副本：不联网、不检索、配音用静音。不改调用方的配置。"""
    local = replace(settings)
    local.auto_fetch_visuals = False
    local.defaults = {**settings.defaults, "tts": "silent"}
    return local


def check_report(report: LocalBenchReport) -> list[str]:
    """检查这次基准有没有暴露回归；返回问题列表，空列表表示正常。

    CI 里的冒烟用：冷启动必须渲染全部镜头，改一个镜头必须只重渲 1 个，缓存必须有复用。
    ``--edits 0`` 时不检查后两条（没有改镜头）。
    """
    problems: list[str] = []
    if report.cold.shots_rendered != report.shots:
        problems.append(f"冷启动应渲染全部 {report.shots} 个镜头，实际 {report.cold.shots_rendered}")
    for run in report.warm:
        if run.shots_rendered != 1:
            problems.append(f"{run.label} 应只重渲 1 个镜头，实际 {run.shots_rendered}")
        if run.reused <= 0:
            problems.append(f"{run.label} 没有任何阶段复用，镜头级缓存可能失效")
    return problems


def _run_once(
    settings: Settings,
    store: ProjectStore,
    project_id: str,
    label: str,
    quality: Quality,
) -> tuple[RunResult, RenderResult]:
    """渲染一次并计时。

    每次都新建 :class:`Renderer`：它的 ``stages`` 记录是这一轮的，跨轮累积会让
    「复用了几个阶段」看起来虚高。
    """
    renderer = Renderer(store, settings, allow_paid=False, quality=quality)
    started = time.perf_counter()
    result = renderer.render(project_id)
    seconds = time.perf_counter() - started
    redone = sorted({stage.kind for stage in result.stages if not stage.reused})
    stage_seconds: dict[str, float] = {}
    for stage in result.stages:
        if stage.reused:
            continue
        stage_seconds[stage.kind] = stage_seconds.get(stage.kind, 0.0) + stage.seconds
    return (
        RunResult(
            label=label,
            seconds=seconds,
            stages=len(result.stages),
            reused=sum(1 for stage in result.stages if stage.reused),
            redone=redone,
            shots_rendered=sum(1 for stage in result.stages if stage.kind == "shot" and not stage.reused),
            stage_seconds={kind: round(value, 3) for kind, value in stage_seconds.items()},
            duration=result.duration,
        ),
        result,
    )


def _clear_cache(store: ProjectStore, project_id: str) -> None:
    """删掉渲染缓存，让下一次是真正的冷启动（素材文件保留，不用重新画）。"""
    cache = store.project_dir(project_id) / "cache"
    if cache.is_dir():
        shutil.rmtree(cache, ignore_errors=True)


def run_local_bench(
    store: ProjectStore,
    settings: Settings,
    out_dir: Path,
    *,
    quality: Quality = "final",
    shots: int = 12,
    runs: int = 3,
    edits: int = 3,
    language: str = "zh-CN",
    keep: bool = False,
    progress=print,
) -> LocalBenchReport:
    """跑一遍本地基准：先冷启动整片，再逐个改一个镜头重渲，取中位数。

    ``shots`` 是基准片的镜头数（默认 12，接近一条 30–40 秒的投放素材；示例项目本身只有 5 个镜头，
    太短会让「改一个镜头」的优势看不出来）。``runs`` 是冷启动跑几次（默认 3，清掉缓存后各跑一遍，
    报告里给出波动范围）；``edits`` 是改几次，每次都改不同的镜头，避免一直命中同一份缓存；
    ``keep`` 为真时保留示例项目，方便打开界面看它是怎么渲染的。
    """
    local = _offline_settings(settings)
    out_dir.mkdir(parents=True, exist_ok=True)

    progress("准备示例项目（本地插画，不联网）")
    build_started = time.perf_counter()
    project = sample.build(store, language=language, tts="silent", shots=shots)
    build_seconds = time.perf_counter() - build_started
    project_id = project.id

    try:
        cold_runs: list[RunResult] = []
        result = None
        for index in range(max(1, runs)):
            _clear_cache(store, project_id)
            progress(
                f"冷启动：渲染整片（第 {index + 1}/{max(1, runs)} 次，"
                f"{len(project.shots)} 个镜头，{quality}）"
            )
            run, result = _run_once(local, store, project_id, f"冷启动 {index + 1}", quality)
            cold_runs.append(run)
        assert result is not None  # runs 至少为 1，上面一定会赋一次值

        warm: list[RunResult] = []
        for index in range(max(0, edits)):
            project = store.load(project_id)
            shot = project.shots[index % len(project.shots)]
            shot.text = f"{EDIT_PREFIX}{shot.text}"
            store.save(project)
            progress(f"改第 {index + 1} 个镜头后重渲（{index + 1}/{edits}）")
            run, result = _run_once(local, store, project_id, f"改镜头 {index + 1}", quality)
            warm.append(run)

        video = store.render_dir(project_id, result.render_id) / result.video
        target = out_dir / "sample.mp4"
        shutil.copyfile(video, target)
        report = LocalBenchReport(
            started_at=time.time(),
            machine=sysinfo.machine_info(),
            ffmpeg=ffmpeg.version(),
            quality=quality,
            language=language,
            shots=len(project.shots),
            build_seconds=build_seconds,
            cold_runs=cold_runs,
            warm=warm,
            peak_rss_mb=sysinfo.peak_rss_mb(),
            video=target.name,
            video_bytes=target.stat().st_size,
        )
        write_json(out_dir / "report.json", report.to_dict())
        (out_dir / "report.md").write_text(format_report(report.to_dict()), encoding="utf-8")
        return report
    finally:
        if not keep:
            try:
                store.delete(project_id)
            except OSError:  # pragma: no cover - Windows 上文件偶尔被占用，删不掉就留着
                pass


def format_report(data: dict) -> str:
    """把本地基准写成 Markdown：先给结论，再给可核对的明细。"""
    summary = data["summary"]
    machine = data["machine"]
    cold = data["cold"]
    colds = data.get("cold_runs") or [cold]
    warm = data["warm"]
    spread = summary.get("cold_spread_pct") or 0.0
    cold_note = (
        f"{len(colds)} 次取中位数，{summary['cold_min']:.1f}–{summary['cold_max']:.1f} 秒，±{spread:.0f}%"
        if len(colds) > 1
        else "单次"
    )
    lines = [
        "# 本地渲染基准",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M', time.localtime(data['started_at']))}",
        f"- 机器：{machine.get('platform', '—')} · {machine.get('cpu_count', '—')} 核 · "
        f"Python {machine.get('python', '—')}",
        f"- FFmpeg：{data['ffmpeg'] or '—'}",
        f"- 画质档：{data['quality']} · 镜头：{data['shots']} 个 · 成片：{summary['video_seconds']} 秒",
        "",
        "## 结论",
        "",
        f"整片冷启动 **{summary['cold_seconds']:.1f} 秒**（{cold_note}；"
        f"{cold.get('shots_rendered', '—')} 个镜头全部渲染）；"
        f"改一个镜头后重渲 **{summary['warm_seconds']:.1f} 秒**，"
        f"只重渲 **{summary.get('warm_shots_rendered', '—')}/{data['shots']}** 个镜头，"
        f"阶段复用率 **{summary['warm_reuse_ratio'] * 100:.0f}%**，"
        f"比整片重来快 **{summary['speedup']:.1f} 倍**。",
        "",
        "| 场景 | 耗时（秒） | 重渲镜头 | 阶段数 | 复用 | 复用率 | 重做的阶段 |",
        "|---|---|---|---|---|---|---|",
    ]
    rows = [*colds, *warm]
    for run in rows:
        lines.append(
            f"| {run['label']} | {run['seconds']:.2f} | {run.get('shots_rendered', '—')} | "
            f"{run['stages']} | {run['reused']} | {run['reuse_ratio'] * 100:.0f}% | "
            f"{', '.join(run['redone']) or '—'} |"
        )
    stages = summary.get("warm_stage_seconds") or {}
    if stages:
        parts = "、".join(f"{kind} {seconds:.1f} 秒" for kind, seconds in stages.items())
        lines.append("")
        lines.append(f"改一镜的 {summary['warm_seconds']:.1f} 秒里：{parts}（各轮中位数）。")
    lines += [
        "",
        f"峰值内存：{_fmt_mb(summary['peak_rss_mb'])}。"
        f"准备示例项目（现画 {data['shots']} 张插画）用了 {data['build_seconds']:.1f} 秒，"
        "不计入上面两行。",
        "",
        "改一个镜头仍要整片重做一次字幕合成与拼接（见「重做的阶段」里的 final-video / assembly），",
        "所以耗时不是 0；镜头越多，省下的比例越大。",
        "",
        "## 怎么复现",
        "",
        "```bash",
        "git clone https://github.com/rushansishui/Shotwright.git",
        "cd Shotwright",
        'pip install -e ".[dev]"',
        "shotwright bench --local",
        "```",
        "",
        "不填任何 Key、不联网、不花钱；只要本机有 FFmpeg。数字随 CPU 核数变化，"
        "看的是「改一个镜头」和「整片重渲」的比例，而不是绝对秒数。",
    ]
    return "\n".join(lines) + "\n"


def _fmt_mb(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f} MB"
