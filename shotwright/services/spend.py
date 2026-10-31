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

from dataclasses import dataclass, field

from shotwright.core.models import Asset, Project, Shot
from shotwright.core.store import ProjectStore, read_json
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


def measure(store: ProjectStore, project: Project) -> SpendMetrics:
    """算出这个项目的四项花费指标。"""
    metrics = SpendMetrics(total=round(project.cost_credits, 4))
    for index, shot in enumerate(project.shots, start=1):
        takes = shot.visual.takes
        if not takes:
            continue
        used = used_assets(shot)
        # 按时间先后，第一次生成的那一版。
        first = min(takes, key=lambda item: item.created_at)
        cost = sum(item.cost for item in takes)
        wasted = sum(item.cost for item in takes if item.asset_id not in used)
        metrics.takes += len(takes)
        metrics.generated_shots += 1
        if first.asset_id in used:
            metrics.first_pass_shots += 1
        metrics.wasted += wasted
        metrics.shots.append(ShotSpend(index, shot.id, len(takes), round(cost, 4), round(wasted, 4)))

    metrics.wasted = round(metrics.wasted, 4)
    duration = _delivered_duration(store, project)
    if duration is None:
        metrics.duration = round(sum(estimated_shot_seconds(project, shot) for shot in project.shots), 2)
    else:
        metrics.duration = round(duration, 2)
        metrics.duration_source = "render"
    return metrics


def _delivered_duration(store: ProjectStore, project: Project) -> float | None:
    """最近一次渲染的成片时长；还没渲染过、或读不出来时返回 None。"""
    if store is None:
        return None
    try:
        directory = store.project_dir(project.id) / "renders"
    except (ValueError, OSError):
        return None
    if not directory.is_dir():
        return None
    latest: tuple[float, float] | None = None
    for child in directory.iterdir():
        data = read_json(child / "render.json")
        if not data:
            continue
        duration = data.get("duration")
        if not isinstance(duration, (int, float)) or duration <= 0:
            continue
        try:
            created = child.stat().st_mtime
        except OSError:
            continue
        if latest is None or created > latest[0]:
            latest = (created, float(duration))
    return None if latest is None else latest[1]


def asset_unused(shot: Shot, asset: Asset) -> bool:
    """这个素材是不是生成了但没用上（某版生成的结果，最后没被选进画面）。"""
    visual = shot.visual
    if asset.id in used_assets(shot):
        return False
    return visual.take(asset.id) is not None
