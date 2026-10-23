"""质量基准集：用固定的几条诉求跑出分镜（可选渲染），算出可对比的质量指标。

每批质量改动完成后跑一次，把结果存档，前后对比提示词长度、镜头衔接、
慢放定格占比、成片响度等指标是否改善。诉求放在 ``resources/bench/briefs.json``，
不要随意改动，否则前后结果没有可比性。

只用到大模型（写分镜）、素材检索和本地渲染；收费的 AI 画面只有显式允许时才会生成。
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from shotwright.config import ROOT_DIR, Settings
from shotwright.core import languages
from shotwright.core.models import Canvas, Project, VoiceSettings
from shotwright.core.store import ProjectStore
from shotwright.media.ffmpeg import measure_loudness
from shotwright.providers import registry
from shotwright.services import localize, prompting, storyboard

BRIEFS_FILE = ROOT_DIR / "resources" / "bench" / "briefs.json"

_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def prompt_size(prompt: str) -> int:
    """画面描述的长度：英文按词计，中日韩按字的一半计（两个汉字大约相当于一个英文词）。"""
    return len(_WORD.findall(prompt)) + len(_CJK.findall(prompt)) // 2


@dataclass
class CaseResult:
    """一条诉求在一种画面比例下的结果。"""

    brief: str
    aspect: str
    project_id: str = ""
    error: str = ""
    shots: int = 0
    target_seconds: float | None = None
    prompt_avg: float = 0.0
    """每个镜头画面描述的平均长度（见 :func:`prompt_size`）。"""
    prompt_min: int = 0
    link_coverage: float = 0.0
    """有衔接说明的相邻镜头占比。"""
    look: bool = False
    """项目有没有全片设定。"""
    issues: list[str] = field(default_factory=list)
    """分镜自检（重写一次后）仍然存在的问题。"""
    rendered: bool = False
    duration: float = 0.0
    loudness: float | None = None
    fill_ratio: float = 0.0
    """成片里靠慢放、定格、循环补出来的时长占比。"""
    blank_ratio: float = 0.0
    """没有画面（纯色）的镜头时长占比。"""
    video: str = ""


@dataclass
class BenchReport:
    started_at: float
    llm: str
    cases: list[CaseResult] = field(default_factory=list)

    def summary(self) -> dict[str, float | None]:
        ok = [case for case in self.cases if not case.error]
        rendered = [case for case in ok if case.rendered]

        def mean(values: list[float]) -> float | None:
            return round(sum(values) / len(values), 3) if values else None

        loud = [case.loudness for case in rendered if case.loudness is not None]
        return {
            "cases": len(self.cases),
            "failed": len(self.cases) - len(ok),
            "prompt_avg": mean([case.prompt_avg for case in ok]),
            "prompt_min": min((case.prompt_min for case in ok), default=None),
            "link_coverage": mean([case.link_coverage for case in ok]),
            "look": mean([1.0 if case.look else 0.0 for case in ok]),
            "issues": mean([float(len(case.issues)) for case in ok]),
            "loudness": mean(loud),
            "loudness_off_target": mean([abs(value + 14.0) for value in loud]),
            "fill_ratio": mean([case.fill_ratio for case in rendered]),
            "blank_ratio": mean([case.blank_ratio for case in rendered]),
        }

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "llm": self.llm,
            "summary": self.summary(),
            "cases": [asdict(case) for case in self.cases],
        }


def load_briefs(path: Path = BRIEFS_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def project_metrics(project: Project, case: CaseResult) -> None:
    """只看项目数据就能算的指标：镜头数、画面描述长度、衔接说明、全片设定。"""
    case.shots = len(project.shots)
    sizes = [prompt_size(shot.visual.prompt) for shot in project.shots]
    case.prompt_avg = round(sum(sizes) / len(sizes), 1) if sizes else 0.0
    case.prompt_min = min(sizes, default=0)
    pairs = max(len(project.shots) - 1, 0)
    if pairs:
        linked = sum(1 for shot in project.shots[1:] if shot.visual.link.strip())
        case.link_coverage = round(linked / pairs, 3)
    case.look = not project.look.is_empty()


def render_metrics(project: Project, timings: list, case: CaseResult) -> None:
    """渲染后的指标：补足（慢放、定格、循环）占比和纯色画面占比。

    ``timings`` 是渲染结果里每个镜头的 :class:`ShotTiming`。
    """
    total = sum(timing.duration for timing in timings) or 1.0
    filled = blank = 0.0
    by_id = {shot.id: shot for shot in project.shots}
    for timing in timings:
        shot = by_id.get(timing.shot_id)
        asset = project.assets.get(shot.visual.selected or "") if shot else None
        if shot is None or asset is None or shot.visual.mode == "color":
            blank += timing.duration
            continue
        if asset.kind != "video":
            continue
        edit = shot.visual.effective_edit()
        source = asset.duration or 0.0
        end = min(edit.trim_end or source, source)
        length = max(end - edit.trim_start, 0.0)
        filled += max(timing.duration - length, 0.0)
    case.fill_ratio = round(filled / total, 3)
    case.blank_ratio = round(blank / total, 3)


def run_bench(
    store: ProjectStore,
    settings: Settings,
    out_dir: Path,
    *,
    render: bool = False,
    allow_paid: bool = False,
    aspects: list[str] | None = None,
    only: list[str] | None = None,
    progress=print,
) -> BenchReport:
    """跑一遍基准集，结果写到 ``out_dir``（report.json、report.md、成片）。"""
    from shotwright.pipeline.render import Renderer

    data = load_briefs()
    language = languages.get(data.get("language") or languages.DEFAULT_LANGUAGE)
    llm_id = settings.default_provider("llm") or ""
    llm = registry.create("llm", None, settings)
    report = BenchReport(started_at=time.time(), llm=llm_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    for brief in data["briefs"]:
        if only and brief["id"] not in only:
            continue
        for aspect in aspects or data.get("aspects") or ["9:16"]:
            case = CaseResult(brief=brief["id"], aspect=aspect, target_seconds=brief.get("target_seconds"))
            report.cases.append(case)
            progress(f"{brief['id']} {aspect}：写分镜")
            try:
                project = Project(
                    title=f"[基准] {brief['id']} {aspect}",
                    subject=brief["subject"],
                    video_type=brief.get("video_type"),
                    target_seconds=brief.get("target_seconds"),
                    canvas=Canvas(aspect=aspect),
                )
                project.voice = VoiceSettings(provider=settings.default_provider("tts") or "edge")
                localize.apply_language(project, language.code)
                board = storyboard.write_storyboard(
                    llm,
                    brief["subject"],
                    language=language.prompt_name,
                    prompt_lang=prompting.prompt_language(project),
                    duration=brief.get("target_seconds"),
                    video_type=brief.get("video_type"),
                    aspect=aspect,
                )
                project.shots, project.look = board.shots, board.look
                case.issues = board.issues
                project = store.create(project)
                case.project_id = project.id
                project_metrics(project, case)
                if render:
                    progress(f"{brief['id']} {aspect}：渲染")
                    rendered = Renderer(store, settings, allow_paid=allow_paid, quality="draft").render(
                        project.id
                    )
                    project = store.load(project.id)
                    video = store.render_dir(project.id, rendered.render_id) / rendered.video
                    target = out_dir / f"{brief['id']}-{aspect.replace(':', 'x')}.mp4"
                    shutil.copyfile(video, target)
                    case.rendered = True
                    case.video = target.name
                    case.duration = rendered.duration
                    case.loudness = measure_loudness(target)
                    render_metrics(project, rendered.timings, case)
            except Exception as exc:  # noqa: BLE001 —— 一条失败不影响其他条
                case.error = str(exc)[:300]
                progress(f"{brief['id']} {aspect}：失败：{case.error}")
    (out_dir / "report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(format_report(report.to_dict()), encoding="utf-8")
    return report


_LABELS = {
    "prompt_avg": "画面描述平均长度（词）",
    "prompt_min": "画面描述最短（词）",
    "link_coverage": "衔接说明覆盖率",
    "look": "有全片设定的比例",
    "issues": "分镜自检剩余问题（个 / 用例）",
    "loudness": "平均响度（LUFS）",
    "loudness_off_target": "响度偏离 -14 的平均值（LU）",
    "fill_ratio": "慢放 / 定格 / 循环占比",
    "blank_ratio": "纯色画面占比",
}


def format_report(data: dict, previous: dict | None = None) -> str:
    """把报告写成 Markdown；给了上一次的报告时并列显示变化。"""
    summary = data["summary"]
    lines = [
        "# 质量基准报告",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M', time.localtime(data['started_at']))}",
        f"- 大模型：{data['llm'] or '未配置'}",
        f"- 用例：{summary['cases']} 个，失败 {summary['failed']} 个",
        "",
    ]
    header = "| 指标 | 本次 |" + (" 上次 | 变化 |" if previous else "")
    lines += [header, "|---|---|" + ("---|---|" if previous else "")]
    for key, label in _LABELS.items():
        value = summary.get(key)
        row = f"| {label} | {_fmt(value)} |"
        if previous:
            old = previous["summary"].get(key)
            delta = value - old if isinstance(value, int | float) and isinstance(old, int | float) else None
            row += f" {_fmt(old)} | {_fmt(delta, signed=True)} |"
        lines.append(row)
    lines += ["", "## 每个用例", "", "| 诉求 | 比例 | 镜头 | 描述均长 | 衔接 | 响度 | 补足占比 | 备注 |"]
    lines.append("|---|---|---|---|---|---|---|---|")
    for case in data["cases"]:
        lines.append(
            f"| {case['brief']} | {case['aspect']} | {case['shots']} | {case['prompt_avg']} | "
            f"{case['link_coverage']} | {_fmt(case['loudness'])} | "
            f"{_fmt(case['fill_ratio']) if case['rendered'] else '—'} | {case['error'] or case['video']} |"
        )
    return "\n".join(lines) + "\n"


def _fmt(value, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        text = f"{value:+.3g}" if signed else f"{value:.3g}"
        return text
    return f"{value:+d}" if signed and isinstance(value, int) else str(value)
