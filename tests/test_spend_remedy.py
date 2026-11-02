"""补救推荐（services/remedy.py）和花费指标（services/spend.py）。"""

from __future__ import annotations

import time

from shotwright.core.models import Project, Shot, ShotVisual, Take
from shotwright.services import remedy, spend

# ---- 补救推荐 ---------------------------------------------------------------


def test_free_remedies_come_first():
    items = remedy.for_issue("short_footage")
    assert items and items[0].cost == "free"
    costs = [item.cost for item in items]
    assert costs == sorted(costs, key=lambda c: {"free": 0, "cheap": 1, "paid": 2}[c])
    # 重新生成要花钱，排在最后。
    assert costs[-1] == "paid"


def test_best_is_the_cheapest():
    best = remedy.best("low_resolution")
    assert best is not None and best.cost == "free"


def test_every_checkup_issue_has_a_remedy():
    # checkup.py 里会报出来的 code 都要有补救建议，否则界面上只剩一句「有问题」。
    from shotwright.services import checkup

    assert all(remedy.for_issue(code) for code in checkup.REMEDIABLE)


def test_risk_remedies_never_cost_money():
    for risk in ("hands", "text", "people", "motion", "camera", "sequence", "duplicate", "too_long"):
        items = remedy.for_issue(f"risk_{risk}")
        assert items, risk
        assert all(item.cost == "free" for item in items)


def test_unknown_codes_have_no_remedy():
    assert remedy.for_issue("not_a_real_code") == []
    assert remedy.best("not_a_real_code") is None


def test_to_dicts_attaches_remedies():
    from shotwright.services.checkup import CheckIssue

    [item] = remedy.to_dicts([CheckIssue("low_resolution", "太糊", 1, {"n": 1})])
    assert item["code"] == "low_resolution"
    assert item["remedies"] and item["remedies"][0]["cost"] == "free"


# ---- 花费指标 ---------------------------------------------------------------


def _project(*takes_per_shot: list[tuple[str, float, bool]]) -> Project:
    """造一个项目：每个镜头若干版本，每版 (素材 id, 花了多少钱, 是否用上)。"""
    project = Project(title="t")
    for index, takes in enumerate(takes_per_shot):
        shot = Shot(text=f"镜头 {index + 1}")
        shot.visual = ShotVisual(mode="ai", prompt="p")
        used = next((asset_id for asset_id, _, on in takes if on), None)
        shot.visual.candidates = [asset_id for asset_id, _, _ in takes]
        shot.visual.selected = used
        base = time.time()
        shot.visual.takes = [
            Take(asset_id=asset_id, kind="video", cost=cost, created_at=base + i)
            for i, (asset_id, cost, _) in enumerate(takes)
        ]
        project.shots.append(shot)
    project.cost_credits = sum(cost for takes in takes_per_shot for _, cost, _ in takes)
    return project


def _measure(project: Project):
    class Store:
        def project_dir(self, _):
            raise OSError("还没有渲染")

    return spend.measure(Store(), project)  # type: ignore[arg-type]


def test_measure_counts_waste_and_first_pass():
    # 镜头 1：第一版就用了，后来又生成一版没用上；镜头 2：第一版没用上，第二版才用。
    project = _project(
        [("a", 1.0, True), ("b", 0.5, False)],
        [("c", 1.0, False), ("d", 1.0, True)],
    )
    metrics = _measure(project)
    assert metrics.total == 3.5
    assert metrics.wasted == 1.5
    assert metrics.generated_shots == 2
    assert metrics.first_pass_shots == 1
    assert metrics.first_pass_rate == 0.5
    # 两个镜头各生成 2 版，平均 2 版。
    assert metrics.average_takes == 2.0


def test_measure_ignores_shots_without_takes():
    project = _project([], [("a", 2.0, True)])
    metrics = _measure(project)
    assert metrics.generated_shots == 1
    assert metrics.takes == 1
    assert metrics.wasted == 0
    assert metrics.first_pass_rate == 1.0


def test_per_second_uses_estimated_duration():
    project = _project([("a", 2.0, True)])
    metrics = _measure(project)
    assert metrics.duration_source == "estimate"
    assert metrics.duration > 0
    assert metrics.per_second == round(2.0 / metrics.duration, 4)


def test_empty_project_has_no_rates():
    metrics = spend.measure(None, Project(title="empty"))  # type: ignore[arg-type]
    assert metrics.generated_shots == 0
    assert metrics.first_pass_rate is None
    assert metrics.average_takes is None
    assert metrics.per_second == 0.0
    assert metrics.to_dict()["first_pass_rate"] is None


def test_keyframe_counts_as_used():
    project = Project(title="t")
    shot = Shot(text="镜头")
    shot.visual = ShotVisual(mode="ai", prompt="p")
    shot.visual.candidates = ["video", "frame"]
    shot.visual.selected = "video"
    shot.visual.first_frame = "frame"
    shot.visual.takes = [
        Take(asset_id="video", kind="video", cost=1.0),
        Take(asset_id="frame", kind="keyframe", cost=0.1),
    ]
    project.shots.append(shot)
    metrics = _measure(project)
    # 关键帧被当作首帧用了，不算浪费。
    assert metrics.wasted == 0
