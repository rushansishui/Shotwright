"""镜头：增删、排序、拆分合并、修改。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from shotwright.api.context import ApiContext
from shotwright.api.schemas import AddShot, Precheck, ReorderShots, SplitShot, UpdateShot
from shotwright.core.models import Project, Shot, ShotVisual
from shotwright.pipeline.shots import MAX_SEQUENCE
from shotwright.services import editing, precheck, visuals


def register(api: APIRouter, ctx: ApiContext) -> None:
    store = ctx.store
    _id = ctx.id
    _mutate = ctx.mutate

    # ---- 镜头 ---------------------------------------------------------------

    @api.post("/projects/{project_id}/shots", status_code=201)
    def add_shot(project_id: str, body: AddShot):
        def change(project: Project) -> None:
            shot = Shot(text=body.text, visual=ShotVisual())
            ids = [s.id for s in project.shots]
            index = ids.index(body.after) + 1 if body.after in ids else len(ids)
            project.shots.insert(index, shot)

        return _mutate(project_id, change)

    @api.put("/projects/{project_id}/shots/order")
    def reorder_shots(project_id: str, body: ReorderShots):
        def change(project: Project) -> None:
            current = {s.id: s for s in project.shots}
            if sorted(body.order) != sorted(current):
                raise ValueError("新顺序必须包含全部镜头且不能重复")
            project.shots = [current[shot_id] for shot_id in body.order]

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/shots/{shot_id}/split")
    def split_shot(project_id: str, shot_id: str, body: SplitShot):
        """在镜头片段的第 ``at`` 秒处拆成两个镜头（只适用于视频镜头）。"""

        def change(project: Project) -> None:
            editing.split_shot(project, _id(shot_id), body.at, store)

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/shots/{shot_id}/split-text")
    def split_text(project_id: str, shot_id: str):
        """把配音文案从中间最近的句子处拆成两个镜头（配音超过 AI 视频单次上限时用）。"""

        def change(project: Project) -> None:
            editing.split_text(project, _id(shot_id))

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/shots/{shot_id}/precheck")
    def precheck_shot(project_id: str, shot_id: str, body: Precheck | None = None):
        """生成前检查：画面描述里容易翻车的内容、配音是否超过模型单次上限。只读，不花钱。"""
        project = store.load(_id(project_id))
        try:
            shot = project.shot(_id(shot_id))
        except KeyError:
            raise HTTPException(404, "镜头不存在") from None
        longest = visuals.shot_max_duration(ctx.settings, shot) if shot.visual.mode == "ai" else None
        risks = precheck.shot_risks(project, shot, prompt=body.prompt if body else None, longest=longest)
        return {"risks": precheck.to_dicts(risks)}

    @api.post("/projects/{project_id}/shots/{shot_id}/merge")
    def merge_shot(project_id: str, shot_id: str):
        """把镜头和后一个镜头合并；同一段视频里相邻的片段会连成一段。"""

        def change(project: Project) -> None:
            editing.merge_with_next(project, _id(shot_id))

        return _mutate(project_id, change)

    @api.patch("/projects/{project_id}/shots/{shot_id}")
    def update_shot(project_id: str, shot_id: str, body: UpdateShot):
        # 只改审片标记时不动镜头状态：审片不影响画面。
        review_only = (
            body.model_fields_set == {"visual"}
            and body.visual is not None
            and (body.visual.model_fields_set <= {"review", "review_note"})
        )

        def change(project: Project) -> None:
            shot = project.shot(_id(shot_id))
            if body.text is not None:
                shot.text = body.text
            if body.role is not None:
                shot.role = body.role
            if body.speech is not None:
                shot.speech = body.speech
            if body.title is not None:
                shot.title = body.title.strip()
            if body.clear_duration:
                shot.duration = None
            elif body.duration is not None:
                shot.duration = body.duration
            if body.clear_voice:
                shot.voice = None
            elif body.voice is not None:
                shot.voice = body.voice
            if body.visual is not None:
                patch = body.visual.model_dump(exclude_unset=True)
                if patch.pop("clear_first_frame", False):
                    patch["first_frame"] = None
                for field in ("selected", "first_frame"):
                    if patch.get(field) and patch[field] not in project.assets:
                        raise ValueError("所选素材不存在")
                if "sequence" in patch:
                    sequence = list(dict.fromkeys(patch["sequence"] or []))
                    if any(item not in project.assets for item in sequence):
                        raise ValueError("所选素材不存在")
                    if any(project.assets[item].kind not in ("video", "image") for item in sequence):
                        raise ValueError("只能多选图片或视频素材")
                    current = patch.get("candidates", shot.visual.candidates)
                    missing = [item for item in sequence if item not in current]
                    if missing:
                        patch["candidates"] = [*current, *missing][-visuals.MAX_CANDIDATES :]
                    patch["sequence"] = sequence
                if patch.get("first_frame") and project.assets[patch["first_frame"]].kind != "image":
                    raise ValueError("首帧必须是图片")
                if "refs" in patch:
                    refs = list(dict.fromkeys(patch["refs"] or []))
                    if any(item not in project.assets for item in refs):
                        raise ValueError("所选素材不存在")
                    if any(project.assets[item].kind != "image" for item in refs):
                        raise ValueError("参考素材必须是图片")
                    patch["refs"] = refs
                if "terms" in patch:
                    patch["terms"] = [t.strip()[:80] for t in patch["terms"] if t.strip()]
                current = patch.get("candidates", shot.visual.candidates)
                if patch.get("selected") and patch["selected"] not in current:
                    patch["candidates"] = [*current, patch["selected"]][-visuals.MAX_CANDIDATES :]
                if patch.pop("clear_trim_end", False):
                    patch["trim_end"] = None
                mode = patch.pop("mode", None)
                if mode is not None:
                    # 切换来源时自动选中该来源的素材（显式传了 selected 时以它为准）。
                    visuals.switch_mode(project, shot, mode)
                edit_fields = {"trim_start", "trim_end", "focus_x", "focus_y"} & patch.keys()
                if edit_fields:
                    # 入出点和取景记在当前选中的素材名下；之前针对别的素材的设置先清掉。
                    target = patch.get("selected", shot.visual.selected)
                    if target is None:
                        raise ValueError("请先选中素材再设置入出点或取景")
                    if shot.visual.edit_asset != target:
                        patch = {"trim_start": 0.0, "trim_end": None, "focus_x": 0.5, "focus_y": 0.5, **patch}
                    patch["edit_asset"] = target
                merged = {**shot.visual.model_dump(), **patch}
                # 选中的素材不重复出现在多选列表里；多选最多 MAX_SEQUENCE 段（含选中的那段）。
                merged["sequence"] = [
                    item for item in merged.get("sequence") or [] if item != merged.get("selected")
                ][: MAX_SEQUENCE - 1]
                if not merged.get("selected"):
                    merged["sequence"] = []
                try:
                    shot.visual = ShotVisual.model_validate(merged)
                except ValidationError as exc:
                    # 只把第一条原因（例如「出点必须晚于入点」）返回给界面，便于翻译。
                    message = str(exc.errors()[0].get("msg", exc))
                    raise ValueError(message.removeprefix("Value error, ")) from None
            if not review_only:
                shot.status, shot.error = "draft", None

        return _mutate(project_id, change)

    @api.delete("/projects/{project_id}/shots/{shot_id}")
    def delete_shot(project_id: str, shot_id: str):
        def change(project: Project) -> None:
            project.shots.remove(project.shot(_id(shot_id)))

        return _mutate(project_id, change)

    @api.delete("/projects/{project_id}/shots/{shot_id}/candidates/{asset_id}")
    def remove_candidate(project_id: str, shot_id: str, asset_id: str):
        """从镜头的候选素材里删掉一个；删的是选中的素材时自动换成下一个。"""

        def change(project: Project) -> None:
            shot = project.shot(_id(shot_id))
            if shot.visual.locked:
                raise HTTPException(409, "镜头已锁定，请先解锁")
            try:
                visuals.remove_candidate(project, shot, _id(asset_id))
            except KeyError:
                raise HTTPException(404, "候选素材不存在") from None
            shot.status, shot.error = "draft", None

        return _mutate(project_id, change)
