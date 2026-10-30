"""镜头内剪辑：拆分、合并镜头。

镜头引用的视频素材用入出点（``trim_start`` / ``trim_end``）选段，所以拆分、合并只需
改入出点，不用重新切文件。主要用来修正原片自动切镜的结果，也适用于任何视频镜头。
"""

from __future__ import annotations

import re

from shotwright.core.ids import new_id
from shotwright.core.models import Asset, Project, Shot, ShotVisual
from shotwright.core.store import ProjectStore
from shotwright.services.visuals import MAX_CANDIDATES

MIN_PART = 0.2
"""拆分后每段至少这么长（秒）。"""


def _clip_range(project: Project, shot: Shot) -> tuple[Asset, float, float]:
    """镜头当前选中的视频素材，以及生效的入点、出点。"""
    visual = shot.visual
    asset = project.assets.get(visual.selected) if visual.selected and visual.mode != "color" else None
    if asset is None or asset.kind != "video":
        raise ValueError("只有视频镜头可以拆分")
    edit = visual.effective_edit()
    end = edit.trim_end if edit.trim_end is not None else asset.duration
    if end is None:
        raise ValueError("无法读取素材时长，不能拆分")
    if asset.duration is not None:
        end = min(end, asset.duration)
    return asset, edit.trim_start, end


def split_shot(project: Project, shot_id: str, at: float, store: ProjectStore | None = None) -> Shot:
    """在镜头片段的第 ``at`` 秒处拆成两个镜头，返回新插入的后半段。

    文案留在前半段；手动设过时长的镜头按两段片段的长度分配时长，跟随配音的保持跟随。
    """
    shot = project.shot(shot_id)
    asset, start, end = _clip_range(project, shot)
    edit = shot.visual.effective_edit()
    # 镜头里有剪掉的片段时，``at`` 是镜头时间，换算成素材里的位置。
    cut = round(edit.to_source_time(start, end, at), 3)
    if cut < start + MIN_PART or cut > end - MIN_PART:
        raise ValueError("拆分点太靠近片段两端")
    base = shot.visual.model_dump()
    first = ShotVisual.model_validate(
        {
            **base,
            "edit_asset": asset.id,
            "trim_start": start,
            "trim_end": cut,
            "skips": edit.skips_within(start, cut),
        }
    )
    second_visual = ShotVisual.model_validate(
        {
            **base,
            "edit_asset": asset.id,
            "trim_start": cut,
            "trim_end": end,
            "skips": edit.skips_within(cut, end),
            "pending": None,
        }
    )
    if shot.duration is not None:
        whole = shot.duration
        shot.duration = round(max(0.1, first.kept_seconds(start, cut)), 3)
        tail = round(max(0.1, second_visual.kept_seconds(cut, end)), 3)
        # 原来时长比片段长（慢放、定格补足的部分）时，多出来的留给后半段。
        tail = max(tail, round(whole - shot.duration, 3))
    else:
        tail = None
    shot.visual = first
    second = Shot(
        id=new_id(), text="", duration=tail, visual=second_visual, voice=shot.voice, speech=shot.speech
    )
    if shot.speech == "original" and store is not None:
        _split_original_text(store, project, shot, second)
    index = project.shots.index(shot)
    project.shots.insert(index + 1, second)
    return second


def merge_with_next(project: Project, shot_id: str) -> Shot:
    """把镜头和它后面的一个镜头合并，返回合并后的镜头。

    两个镜头是同一段视频里相邻的片段时，入出点直接连起来；否则保留前一个镜头的画面。
    文案拼在一起，两边都手动设过时长时相加，否则跟随配音。
    """
    shot = project.shot(shot_id)
    index = project.shots.index(shot)
    if index + 1 >= len(project.shots):
        raise ValueError("后面没有可以合并的镜头")
    following = project.shots[index + 1]

    a, b = shot.visual, following.visual
    if (
        a.mode != "color"
        and a.selected is not None
        and a.selected == b.selected
        and project.assets.get(a.selected) is not None
        and project.assets[a.selected].kind == "video"
    ):
        _, a_start, a_end = _clip_range(project, shot)
        _, b_start, b_end = _clip_range(project, following)
        if b_start > a_end - 0.05:
            # 首尾相接直接连起来；中间隔着一段（按文字剪辑剪掉的）时，那一段记为剪掉的片段。
            skips = [
                *a.effective_edit().skips_within(a_start, a_end),
                *b.effective_edit().skips_within(b_start, b_end),
            ]
            if b_start - a_end >= 0.05:
                skips.append((a_end, b_start))
            shot.visual = ShotVisual.model_validate(
                {
                    **a.model_dump(),
                    "edit_asset": a.selected,
                    "trim_start": a_start,
                    "trim_end": b_end,
                    "skips": sorted(skips),
                }
            )

    candidates = list(shot.visual.candidates)
    for asset_id in b.candidates:
        if asset_id not in candidates:
            candidates.append(asset_id)
    shot.visual.candidates = candidates[-MAX_CANDIDATES:]
    if shot.visual.selected is not None and shot.visual.selected not in shot.visual.candidates:
        shot.visual.candidates = [*shot.visual.candidates[1:], shot.visual.selected]

    left, right = shot.text.strip(), following.text.strip()
    if left and right:
        separator = " " if left[-1].isascii() and right[0].isascii() else ""
        shot.text = left + separator + right
    else:
        shot.text = left or right
    if shot.duration is not None and following.duration is not None:
        shot.duration = min(120.0, round(shot.duration + following.duration, 3))
    else:
        shot.duration = None
    project.shots.remove(following)
    return shot


def _split_original_text(store: ProjectStore, project: Project, first: Shot, second: Shot) -> None:
    """原声镜头拆开后，按识别出的逐词时间把字幕文字分到两段；没有识别结果时文字留在前半段。"""
    from shotwright.services import transcribe

    parts = [transcribe.shot_words(store, project, target) for target in (first, second)]
    if not any(parts):
        return
    for target, words in zip((first, second), parts, strict=True):
        target.text = transcribe.join_words([w[0] for w in words])


_CLAUSE_END = re.compile(r"(?<=[。！？!?；;…，,、：:])\s*|(?<=\.)\s+")
"""文案里可以断开的位置：句末，其次是逗号这类分句处。"""


def split_text(project: Project, shot_id: str) -> Shot:
    """把一个配音镜头的文案从中间最近的句子（或分句）处拆成两个镜头，返回新插入的后半段。

    配音比 AI 视频模型一次能生成的长度还长时用：拆开后每个镜头单独生成，比分段续生成
    便宜，衔接也自然。后半段沿用画面来源、画面描述、运镜和服务选择，但不带已经生成的
    候选素材，需要单独生成；两个镜头都跟随配音时长。
    """
    shot = project.shot(shot_id)
    if shot.speech == "original":
        raise ValueError("原声镜头请在画面上按时间拆分")
    text = shot.text.strip()
    cuts = [m.end() for m in _CLAUSE_END.finditer(text) if 0 < m.end() < len(text)]
    if not cuts:
        raise ValueError("这句文案里没有可以断开的句号或逗号，请手动拆分或删减")
    middle = len(text) / 2
    sentence = [c for c in cuts if text[:c].rstrip()[-1:] in "。！？!?；;….\n"]
    # 优先在句末断开；句末离中间太远（超过三分之一）时改在逗号处断开。
    pool = sentence if sentence and min(abs(c - middle) for c in sentence) <= len(text) / 3 else cuts
    cut = min(pool, key=lambda c: abs(c - middle))
    head, tail = text[:cut].strip(), text[cut:].strip()
    visual = shot.visual
    second = Shot(
        id=new_id(),
        text=tail,
        role=shot.role,
        voice=shot.voice,
        visual=ShotVisual(
            mode=visual.mode,
            prompt=visual.prompt,
            image_prompt=visual.image_prompt,
            camera=visual.camera,
            terms=list(visual.terms),
            provider=visual.provider,
            color=visual.color,
            refs=list(visual.refs or []),
        ),
    )
    shot.text = head
    shot.duration = None
    project.shots.insert(project.shots.index(shot) + 1, second)
    return second
