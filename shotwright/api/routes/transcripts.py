"""剪辑：识别原声、按文字剪。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from shotwright.api.context import ApiContext
from shotwright.api.schemas import CutDraft, CutRequest, TranscribeRequest
from shotwright.core.models import Project
from shotwright.providers import registry
from shotwright.providers.base import ProviderError
from shotwright.services import transcribe


def register(api: APIRouter, ctx: ApiContext) -> None:
    store = ctx.store
    settings = ctx.settings
    _id = ctx.id
    _mutate = ctx.mutate

    # ---- 剪辑：识别原声，按文字剪 ---------------------------------------------------

    def _media_asset(project: Project, asset_id: str):
        asset = project.assets.get(_id(asset_id))
        if asset is None:
            raise HTTPException(404, "素材不存在")
        return asset

    @api.get("/projects/{project_id}/transcripts/{asset_id}")
    def read_transcript(project_id: str, asset_id: str):
        """识别状态和结果；完成时附带语气词的下标（剪辑页一键勾选）。"""
        project = store.load(_id(project_id))
        _media_asset(project, asset_id)
        data = transcribe.load(store, project.id, asset_id)
        if data.get("state") == "done":
            words = transcribe.words_of(data)
            data = {
                **data,
                "fillers": transcribe.filler_indexes(words, data.get("language") or project.language),
            }
        return data

    @api.post("/projects/{project_id}/transcripts/{asset_id}")
    def start_transcript(project_id: str, asset_id: str, body: TranscribeRequest | None = None):
        """开始识别（后台进行），之后轮询 GET 查看进度。"""
        project = store.load(_id(project_id))
        _media_asset(project, asset_id)
        body = body or TranscribeRequest()
        return transcribe.start(
            store, settings, project, asset_id, provider_id=body.provider, language=body.language
        )

    @api.post("/projects/{project_id}/transcripts/{asset_id}/retakes")
    def suggest_retakes(project_id: str, asset_id: str):
        """AI 去口误：找出重说、说一半放弃、和内容无关的句子，返回其中所有词的下标（不收费的大模型调用）。"""
        project = store.load(_id(project_id))
        _media_asset(project, asset_id)
        data = transcribe.load(store, project.id, asset_id)
        if data.get("state") != "done":
            raise HTTPException(409, "这段原片还没有识别完成")
        llm = registry.create("llm", None, settings)
        return {"remove": transcribe.suggest_retakes(llm, data)}  # type: ignore[arg-type]

    @api.put("/projects/{project_id}/transcripts/{asset_id}/draft")
    def save_cut_draft(project_id: str, asset_id: str, body: CutDraft):
        """保存剪辑进度：页面上每次删词、改字后自动保存。"""
        project = store.load(_id(project_id))
        _media_asset(project, asset_id)
        try:
            return transcribe.save_draft(store, project.id, asset_id, body.model_dump())
        except ProviderError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api.post("/projects/{project_id}/cut")
    def cut_by_text(project_id: str, body: CutRequest):
        """按剪辑结果生成原声镜头，替换项目里现有的镜头。"""
        texts: dict[int, str] = {}
        for key, value in body.texts.items():
            try:
                texts[int(key)] = value[:200]
            except ValueError:
                raise HTTPException(422, "改字的下标不对") from None

        def change(project: Project) -> None:
            _media_asset(project, body.asset_id)
            transcribe.build_shots(
                store,
                project,
                body.asset_id,
                removed=set(body.removed),
                max_pause=body.max_pause,
                split_scenes=body.split_scenes,
                texts=texts,
            )
            # 生成分镜用的设置也记为剪辑进度，回到剪辑页时还是这一版。
            transcribe.save_draft(
                store,
                project.id,
                body.asset_id,
                {
                    "removed": body.removed,
                    "texts": body.texts,
                    "pauses": body.max_pause is not None,
                    "max_pause": body.max_pause,
                    "scenes": body.split_scenes,
                },
            )
            project.video_type = "footage"
            project.origin = "cut"

        return _mutate(project_id, change)

    # 正在检索或生成的镜头：页面刷新后仍能显示"生成中"，并拒绝重复提交。
