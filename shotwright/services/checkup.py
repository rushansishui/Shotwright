"""渲染前体检：渲染之前把容易出问题的地方列出来，用户决定要不要先改。

只读项目、不改任何东西，也不调用外部服务。检查项：
缺画面、AI 镜头没有画面描述、素材明显比配音短（会大段定格或循环）、素材分辨率
不足、叠加文字与字幕或彼此重叠、按当前设置渲染会超出费用上限、AI 镜头多但没有
全片设定。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from shotwright.config import Settings
from shotwright.core import platforms
from shotwright.core.models import MIN_SLOW_SPEED, Project
from shotwright.services.durations import estimated_shot_seconds

LOW_RESOLUTION = 0.6
"""素材短边不到画布短边的这个比例时，放大后会明显发虚。"""
SHORT_FOOTAGE = 1.0
"""慢放到最慢之后仍差这么多秒以上，才算「素材明显比配音短」。"""


@dataclass
class CheckIssue:
    code: str
    message: str
    shot: int | None = None
    """镜头序号（从 1 开始）；整片问题为空。"""
    params: dict | None = None
    """界面按 code 和这些参数显示本地化的文字（message 是中文版，供命令行使用）。"""
    level: str = "warn"


def check_project(project: Project, settings: Settings, *, quality: str = "final") -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    short_side = min(project.canvas.width, project.canvas.height)
    ai_shots = 0
    for index, shot in enumerate(project.shots, start=1):
        visual = shot.visual
        if visual.mode in ("ai", "image"):
            ai_shots += 1
            if not (visual.prompt.strip() or visual.image_prompt.strip()):
                issues.append(CheckIssue("no_prompt", f"镜头 {index} 是 AI 画面，但没有画面描述", index, {"n": index}))
        if visual.mode == "color":
            continue
        asset = project.assets.get(visual.selected or "")
        if asset is None or asset.kind not in ("video", "image"):
            issues.append(
                CheckIssue(
                    "no_visual", f"镜头 {index} 还没有画面，渲染时会自动检索素材，拿不到就用纯色", index, {"n": index}
                )
            )
            continue
        if asset.width and asset.height and min(asset.width, asset.height) < short_side * LOW_RESOLUTION:
            issues.append(
                CheckIssue(
                    "low_resolution",
                    f"镜头 {index} 的素材只有 {asset.width}×{asset.height}，放大到画布后会发虚",
                    index,
                    {"n": index, "size": f"{asset.width}×{asset.height}"},
                )
            )
        if asset.kind == "video" and asset.duration:
            edit = visual.effective_edit()
            end = min(edit.trim_end or asset.duration, asset.duration)
            length = max(end - edit.trim_start, 0.0)
            needed = estimated_shot_seconds(project, shot)
            gap = needed - length / (MIN_SLOW_SPEED if visual.fill == "slow_hold" else 1.0)
            if gap > SHORT_FOOTAGE:
                how = {"slow_hold": "慢放后定格", "hold": "定格", "loop": "循环"}[visual.fill]
                issues.append(
                    CheckIssue(
                        "short_footage",
                        f"镜头 {index} 的素材约 {length:.1f} 秒，配音约 {needed:.1f} 秒，会{how}补足约 {gap:.1f} 秒",
                        index,
                        {"n": index, "footage": round(length, 1), "voice": round(needed, 1), "gap": round(gap, 1)},
                    )
                )
    issues += _overlay_issues(project)
    spec = platforms.platform(project.delivery)
    if project.canvas.aspect not in spec.aspects:
        issues.append(
            CheckIssue(
                "platform_aspect",
                f"交付平台「{spec.name}」推荐 {' / '.join(spec.aspects)}，当前画面比例是 {project.canvas.aspect}",
                None,
                {"platform": spec.name, "aspects": " / ".join(spec.aspects), "aspect": project.canvas.aspect},
            )
        )
    if ai_shots >= 2 and project.look.is_empty():
        issues.append(CheckIssue("no_look", "有多个 AI 镜头但没有全片设定，人物和产品在各镜头可能不一致", None, {}))
    if project.budget is not None:
        from shotwright.services import cost

        estimate = cost.estimate_render_cost(project, settings, quality).total
        if estimate > 0 and project.cost_credits + estimate > project.budget:
            issues.append(
                CheckIssue(
                    "over_budget",
                    f"按当前设置生成缺少的 AI 画面约需 {estimate:.2f} 元，会超出费用上限 {project.budget:.2f} 元"
                    f"（已花 {project.cost_credits:.2f} 元）",
                    None,
                    {
                        "estimate": f"{estimate:.2f}",
                        "budget": f"{project.budget:.2f}",
                        "spent": f"{project.cost_credits:.2f}",
                    },
                )
            )
    return issues


def _overlay_issues(project: Project) -> list[CheckIssue]:
    """叠加文字和字幕在同一位置、时间重叠，或叠加文字彼此重叠。"""
    issues: list[CheckIssue] = []
    total = sum(estimated_shot_seconds(project, shot) for shot in project.shots)
    overlays = [o for o in project.overlays if o.enabled and o.text.strip()]

    def span(item) -> tuple[float, float]:
        end = item.start + item.duration if item.duration else total
        return item.start, end

    def overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
        return a[0] < b[1] - 0.05 and b[0] < a[1] - 0.05

    if project.subtitle.enabled:
        for item in overlays:
            if item.position == project.subtitle.position:
                issues.append(
                    CheckIssue(
                        "overlay_subtitle",
                        f"叠加文字「{item.text[:12]}」和字幕都在画面{_POSITIONS[item.position]}，可能互相遮挡",
                        None,
                        {"text": item.text[:12], "position": item.position},
                    )
                )
    for i, first in enumerate(overlays):
        for second in overlays[i + 1 :]:
            if first.position == second.position and overlap(span(first), span(second)):
                issues.append(
                    CheckIssue(
                        "overlay_overlap",
                        f"叠加文字「{first.text[:12]}」和「{second.text[:12]}」同时出现在画面{_POSITIONS[first.position]}",
                        None,
                        {"a": first.text[:12], "b": second.text[:12], "position": first.position},
                    )
                )
    return issues


_POSITIONS = {"top": "顶部", "middle": "中间", "bottom": "底部"}


def to_dicts(issues: list[CheckIssue]) -> list[dict]:
    return [asdict(issue) for issue in issues]
