"""花费指标：钱花得值不值。

四个数（和路线图里的一致）：

- 每秒成片花费：项目累计花掉的钱 ÷ 成片时长；
- 首次可用率：一生成就用了的镜头占已生成镜头的比例；
- 平均重做次数：平均每个已生成镜头生成过几版；
- 浪费金额：生成了但最后没用上的版本花掉的钱。

数据都来自项目本身：每次收费生成成功后累加的 ``cost_credits``、以及每个镜头
版本记录 ``Take`` 上的 ``cost``。不调用外部服务，只看已落盘的数据。

成片时长优先用最近一次渲染的结果；还没渲染过时用配音估算的时长。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from shotwright.core.models import Asset, Project, Shot
from shotwright.core.store import ProjectStore, read_json, write_json
from shotwright.services.durations import estimated_shot_seconds


@dataclass
class ShotSpend:
    shot_index: int
    shot_id: str
    takes: int
    """这个镜头生成过几版。"""
    cost: float
    """生成这些版本一共花了多少（元）。"""
    wasted: float
    """其中没用上的版本花了多少（元）。"""


@dataclass
class SpendMetrics:
    total: float = 0.0
    """项目累计花费（元）。"""
    wasted: float = 0.0
    """生成了但没用上的花费（元）。"""
    generated_shots: int = 0
    """生成过画面的镜头数。"""
    first_pass_shots: int = 0
    """第一次生成的那版就用了的镜头数。"""
    takes: int = 0
    """所有镜头加起来的版本数。"""
    duration: float = 0.0
    """成片时长（秒）；还没渲染时按配音估算。"""
    duration_source: str = "estimate"
    """``render``：来自最近一次渲染；``estimate``：按配音估算。"""
    shots: list[ShotSpend] = field(default_factory=list)

    @property
    def per_second(self) -> float:
        """每秒成片花费（元）。"""
        return round(self.total / self.duration, 4) if self.duration > 0 else 0.0

    @property
    def first_pass_rate(self) -> float | None:
        """首次可用率（0–1）；没有生成过的镜头时为空。"""
        return round(self.first_pass_shots / self.generated_shots, 4) if self.generated_shots else None

    @property
    def average_takes(self) -> float | None:
        """平均每个已生成镜头生成过几版。"""
        return round(self.takes / self.generated_shots, 2) if self.generated_shots else None

    def to_dict(self) -> dict:
        return {
            "total": round(self.total, 4),
            "wasted": round(self.wasted, 4),
            "per_second": self.per_second,
            "first_pass_rate": self.first_pass_rate,
            "average_takes": self.average_takes,
            "generated_shots": self.generated_shots,
            "takes": self.takes,
            "duration": round(self.duration, 2),
            "duration_source": self.duration_source,
            "currency": "CNY",
            "shots": [
                {
                    "shot_index": item.shot_index,
                    "shot_id": item.shot_id,
                    "takes": item.takes,
                    "cost": round(item.cost, 4),
                    "wasted": round(item.wasted, 4),
                }
                for item in self.shots
            ],
        }


def used_assets(shot: Shot) -> set[str]:
    """这个镜头最终用到的素材：选中的、依次播放的、以及 AI 视频的首帧。"""
    visual = shot.visual
    used = set(visual.sequence)
    if visual.selected:
        used.add(visual.selected)
    if visual.first_frame:
        used.add(visual.first_frame)
    return used


def measure(store: ProjectStore, project: Project, *, render_id: str | None = None) -> SpendMetrics:
    """算出这个项目的四项花费指标。

    花费按全部版本累加（关键帧也是真花钱的），但「生成过几版」「首次可用率」只看
    正式画面：关键帧只是首帧的备选，混进来会让默认的「先出 3 张关键帧」把平均重做
    次数抬高好几倍，指标就失去了比较的意义。

    ``render_id`` 指定用哪一次渲染的成片时长（写回执时用，避免并发下拿到别的渲染）；
    为空时取最近一次渲染。
    """
    metrics = SpendMetrics(total=round(project.cost_credits, 4))
    for index, shot in enumerate(project.shots, start=1):
        takes = shot.visual.takes
        if not takes:
            continue
        generations = shot.visual.generation_takes()
        used = used_assets(shot)
        cost = sum(item.cost for item in takes)
        wasted = sum(item.cost for item in takes if item.asset_id not in used)
        metrics.wasted += wasted
        metrics.takes += len(generations)
        if generations:
            # 按时间先后，第一版正式画面。
            first = min(generations, key=lambda item: item.created_at)
            metrics.generated_shots += 1
            if first.asset_id in used:
                metrics.first_pass_shots += 1
        metrics.shots.append(ShotSpend(index, shot.id, len(generations), round(cost, 4), round(wasted, 4)))

    metrics.wasted = round(metrics.wasted, 4)
    duration = _delivered_duration(store, project, render_id)
    if duration is None:
        metrics.duration = round(sum(estimated_shot_seconds(project, shot) for shot in project.shots), 2)
    else:
        metrics.duration = round(duration, 2)
        metrics.duration_source = "render"
    return metrics


def _delivered_duration(store: ProjectStore, project: Project, render_id: str | None = None) -> float | None:
    """某次渲染的成片时长；没指定时用最近一次。还没渲染过、或读不出来时返回 None。"""
    if store is None:
        return None
    try:
        if render_id:
            data = read_json(store.render_dir(project.id, render_id) / "render.json")
            return _positive_duration((data or {}).get("duration"))
        directory = store.project_dir(project.id) / "renders"
    except (ValueError, OSError):
        return None
    if not directory.is_dir():
        return None
    latest: tuple[float, float] | None = None
    for child in directory.iterdir():
        duration = _positive_duration((read_json(child / "render.json") or {}).get("duration"))
        if duration is None:
            continue
        try:
            created = child.stat().st_mtime
        except OSError:
            continue
        if latest is None or created > latest[0]:
            latest = (created, duration)
    return None if latest is None else latest[1]


def _positive_duration(value) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def asset_unused(shot: Shot, asset: Asset) -> bool:
    """这个素材是不是生成了但没用上（某版生成的结果，最后没被选进画面）。"""
    visual = shot.visual
    if asset.id in used_assets(shot):
        return False
    return visual.take(asset.id) is not None


# ---- 花费回执 ---------------------------------------------------------------
#
# 回执是把「花了多少钱」变成一张能截图、能转发的账单：每个镜头生成过几版、哪几版
# 白花了，最后汇总成四个数。数字全部来自项目文件，不调用任何外部服务。


def format_receipt(project: Project, metrics: SpendMetrics) -> str:
    """终端里能直接看的花费回执（等宽对齐，中文按两格算）。"""
    head = f"《{project.title}》 · {_duration(metrics)} · {len(project.shots)} 个镜头"
    lines = [head, f"花费回执 · {time.strftime('%Y-%m-%d %H:%M')}", "─" * 40]
    if not metrics.generated_shots:
        lines.append("还没有收费生成，这个项目一分钱没花。")
        lines.append("（配音用免费服务、画面用素材库或上传文件时不产生费用。）")
        return "\n".join(lines)

    lines += [
        f"累计花费      ¥{metrics.total:.2f}",
        f"其中浪费      ¥{metrics.wasted:.2f}（{_waste_ratio(metrics)}）",
        f"每秒成片      ¥{metrics.per_second:.3f}",
        f"首次可用率    {_first_pass(metrics)}",
        f"平均重做      {metrics.average_takes:.2f} 版 / 镜头",
        "",
        "逐镜头",
    ]
    for item in metrics.shots:
        if item.takes == 0:
            continue
        wasted = f"浪费 ¥{item.wasted:.2f}" if item.wasted > 0 else "都算数"
        lines.append(f"  #{item.shot_index:<2} ¥{item.cost:>5.2f}  {item.takes:>2} 版  {wasted}")
    return "\n".join(lines)


def receipt_markdown(project: Project, metrics: SpendMetrics) -> str:
    """同上一份回执，Markdown 版：贴到 issue、周报或 README 里用。"""
    lines = [
        f"### 花费回执 · {project.title}",
        "",
        f"- 成片 {_duration(metrics)}，{len(project.shots)} 个镜头",
        f"- 累计花费 **¥{metrics.total:.2f}**，其中浪费 ¥{metrics.wasted:.2f}（{_waste_ratio(metrics)}）",
        f"- 每秒成片 ¥{metrics.per_second:.3f}",
        f"- 首次可用率 {_first_pass(metrics)}",
        f"- 平均重做 {metrics.average_takes:.2f} 版 / 镜头" if metrics.average_takes else "- 还没有生成记录",
        "",
        "| 镜头 | 花费 | 版本 | 浪费 |",
        "|---|---|---|---|",
    ]
    for item in metrics.shots:
        if item.takes == 0:
            continue
        lines.append(f"| #{item.shot_index} | ¥{item.cost:.2f} | {item.takes} | ¥{item.wasted:.2f} |")
    return "\n".join(lines) + "\n"


def write_receipt(store: ProjectStore, project: Project, render_id: str | None = None) -> Path | None:
    """把花费回执写进渲染目录的 ``cost.json``，和成片放在一起。

    ``render_id`` 为空时写到最近一次渲染；还没有渲染过时返回 ``None``。
    渲染成功后才调用，失败不影响成片。
    """
    directory = _render_dir(store, project, render_id)
    if directory is None:
        return None
    metrics = measure(store, project, render_id=directory.name)
    path = directory / "cost.json"
    write_json(
        path,
        {
            **metrics.to_dict(),
            "project_id": project.id,
            "project_title": project.title,
            "render_id": directory.name,
            "generated_at": round(time.time(), 3),
            "receipt": format_receipt(project, metrics),
        },
    )
    return path


def _render_dir(store: ProjectStore, project: Project, render_id: str | None) -> Path | None:
    try:
        if render_id:
            directory = store.render_dir(project.id, render_id)
            return directory if directory.is_dir() else None
        root = store.project_dir(project.id) / "renders"
        if not root.is_dir():
            return None
        latest: tuple[float, Path] | None = None
        for child in root.iterdir():
            if not (child / "render.json").is_file():
                continue
            created = child.stat().st_mtime
            if latest is None or created > latest[0]:
                latest = (created, child)
        return None if latest is None else latest[1]
    except (OSError, ValueError):
        return None


def _duration(metrics: SpendMetrics) -> str:
    source = "成片" if metrics.duration_source == "render" else "估算"
    return f"{metrics.duration:.1f} 秒（{source}）"


def _waste_ratio(metrics: SpendMetrics) -> str:
    if metrics.total <= 0:
        return "0%"
    return f"{round(metrics.wasted / metrics.total * 100)}%"


def _first_pass(metrics: SpendMetrics) -> str:
    if metrics.first_pass_rate is None:
        return "—"
    return f"{round(metrics.first_pass_rate * 100)}%（{metrics.first_pass_shots}/{metrics.generated_shots} 个镜头）"
