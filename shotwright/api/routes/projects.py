"""项目：列表、新建、诉求与分镜、全片设定、版本、费用预估与体检。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from shotwright.api.context import ApiContext
from shotwright.api.schemas import (
    ApplyMaterials,
    ChargeConsent,
    CreateProject,
    CreateVersion,
    DuplicateProject,
    NormalizeBrief,
    PreviewVoice,
    ScriptRequest,
    StoryboardRequest,
    SuggestTitles,
    UpdateProject,
)
from shotwright.core import languages
from shotwright.core.models import Project, VoiceSettings
from shotwright.core.store import read_json
from shotwright.media import ffmpeg
from shotwright.pipeline import voice as voice_stage
from shotwright.pipeline.render_types import RenderError
from shotwright.providers import registry
from shotwright.providers.base import ProviderError, ProviderNotConfigured
from shotwright.services import brief as brief_service
from shotwright.services import checkup, localize, prompting, pronounce, readiness, storyboard, visuals
from shotwright.services import cost as cost_service
from shotwright.services import inspect as inspect_service
from shotwright.services import titles as titles_service
from shotwright.services.duplicate import duplicate_project


def register(api: APIRouter, ctx: ApiContext) -> None:
    store = ctx.store
    settings = ctx.settings
    jobs = ctx.jobs
    _id = ctx.id
    _mutate = ctx.mutate

    # ---- 项目 ---------------------------------------------------------------

    @api.get("/projects")
    def list_projects():
        result = []
        for p in store.list():
            latest = _latest_render(p.id)
            source = _cover_source(p)
            result.append(
                {
                    "id": p.id,
                    "title": p.title or p.subject or "未命名项目",
                    "shots": len(p.shots),
                    "aspect": p.canvas.aspect,
                    "language": languages.normalize(p.language),
                    "group": p.group or p.id,
                    "origin": _origin(p),
                    "updated_at": p.updated_at,
                    "created_at": p.created_at,
                    # 最近一次成片：时长、生成时间，以及之后有没有再改过项目。
                    "render": (
                        {
                            "id": latest["render_id"],
                            "duration": latest.get("duration"),
                            "created_at": latest["created_at"],
                            "stale": latest.get("revision") != p.revision,
                        }
                        if latest
                        else None
                    ),
                    # 剪辑任务还没生成镜头时，从任务列表回到剪辑页要用到原片的素材 id。
                    "source_asset": next(
                        (a.id for a in p.assets.values() if a.kind in ("video", "audio")), None
                    )
                    if not p.shots
                    else None,
                    # 封面：最近的成片或第一个有画面的镜头；地址带上来源，换了来源浏览器会重新取。
                    "cover": f"/api/v2/projects/{p.id}/cover?k={source[1]}" if source else None,
                }
            )
        return result

    def _origin(p: Project) -> str:
        """任务来自创作还是剪辑。旧版剪辑页建的项目没有记 origin：有原声镜头、或识别过原片的算剪辑。"""
        if p.origin == "cut" or any(s.speech == "original" for s in p.shots):
            return "cut"
        transcripts = store.project_dir(p.id) / "cache" / "transcripts"
        if not p.subject and not p.idea and transcripts.is_dir() and any(transcripts.iterdir()):
            return "cut"
        return "create"

    def _latest_render(project_id: str) -> dict | None:
        directory = store.project_dir(project_id) / "renders"
        best: dict | None = None
        if directory.is_dir():
            for child in directory.iterdir():
                video = child / "video.mp4"
                if not video.is_file():
                    continue
                created = video.stat().st_mtime
                if best is not None and created <= best["created_at"]:
                    continue
                data = read_json(child / "render.json")
                if data and data.get("render_id"):
                    best = {**data, "created_at": created}
        return best

    def _cover_source(project: Project) -> tuple[Path, str] | None:
        """封面取自哪个文件：最近的成片，没有成片时用第一个有画面的镜头。返回（文件, 缓存键）。"""
        latest = _latest_render(project.id)
        if latest:
            video = store.project_dir(project.id) / "renders" / latest["render_id"] / "video.mp4"
            return video, f"r{latest['render_id']}"
        for shot in project.shots:
            visual = shot.visual
            asset = project.assets.get(visual.selected or "") if visual.mode != "color" else None
            if asset is not None and asset.kind in ("image", "video"):
                path = store.asset_path(project, asset.id)
                if path.is_file():
                    return path, f"a{asset.id}"
        return None

    @api.get("/projects/{project_id}/cover")
    def project_cover(project_id: str):
        """项目封面（480 像素宽的 JPEG），按来源缓存。"""
        project = store.load(_id(project_id))
        source = _cover_source(project)
        if source is None:
            raise HTTPException(404, "项目还没有画面")
        path, key = source
        target = store.cache_dir(project.id, "cover") / f"{key}.jpg"
        if not target.is_file():
            scale = "scale='min(480,iw)':-2"
            temp = target.with_suffix(".tmp.jpg")
            attempts = (
                [["-ss", "1", "-i", str(path)], ["-i", str(path)]]
                if path.suffix != ".png"
                else [["-i", str(path)]]
            )
            for inputs in attempts:
                try:
                    ffmpeg.run_ffmpeg([*inputs, "-frames:v", "1", "-vf", scale, "-q:v", "4", str(temp)])
                except Exception:  # noqa: BLE001 —— 短视频从第 1 秒取不到帧时退回第一帧
                    continue
                if temp.is_file() and temp.stat().st_size > 0:
                    temp.replace(target)
                    break
            if not target.is_file():
                raise HTTPException(404, "封面生成失败")
        return FileResponse(target, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @api.post("/projects", status_code=201)
    def create_project(body: CreateProject):
        lang = languages.get(body.language)
        project = Project(
            title=body.title,
            subject=body.subject,
            idea=body.idea,
            target_seconds=body.duration,
            video_type=body.video_type,
            origin=body.origin,
        )
        # 新项目要先确认素材（全片设定）才能生成 AI 画面。
        project.look.confirmed = False
        if body.canvas:
            project.canvas = body.canvas
        if not body.voice and settings.default_provider("tts"):
            # 没指定配音时用配置里 [defaults] tts 的服务，而不是固定用 Edge。
            project.voice = VoiceSettings(provider=settings.default_provider("tts"))
        localize.apply_language(project, lang.code)
        if body.voice:
            # 用户显式指定的配音优先于语言默认值。
            project.voice = body.voice
        if body.mode == "llm":
            if not body.subject.strip():
                raise HTTPException(422, "请填写视频诉求")
            llm = registry.create("llm", None, settings)
            board = storyboard.write_storyboard(
                llm,
                body.subject.strip(),
                language=lang.prompt_name,
                prompt_lang=prompting.prompt_language(project),
                shots=body.shots,
                duration=body.duration,
                video_type=body.video_type,
                aspect=project.canvas.aspect,
            )
            project.title = project.title or board.title
            project.shots = board.shots
            project.look = board.look
            project.look.confirmed = False
            if body.video_type:
                storyboard.suggest_sources(project.shots, visuals.available_sources(settings), project.look)
        elif body.mode == "split":
            if not body.script.strip():
                raise HTTPException(422, "请填写视频文案")
            project.shots = storyboard.split_script(body.script)
        # 创作记录：生成分镜时的文案，之后改镜头不影响。
        project.script = [shot.text for shot in project.shots if shot.text.strip()][:60]
        project.title = project.title or body.subject[:40] or "未命名项目"
        return store.create(project)

    def _materials(items) -> list[brief_service.Material]:
        return [
            brief_service.Material(
                kind=item.kind, role=item.role, name=item.name, note=item.note, asset_id=item.asset_id
            )
            for item in items
        ]

    @api.post("/brief/normalize")
    def normalize_brief(body: NormalizeBrief):
        """把用户随手写的想法按视频类型的模板整理成完整诉求。不建项目、不收费。"""
        lang = languages.get(body.language)
        llm = registry.create("llm", None, settings)
        text = brief_service.normalize_brief(
            llm,  # type: ignore[arg-type]
            body.idea,
            template=body.template,
            video_type=body.video_type,
            language=lang.prompt_name,
            aspect=body.aspect,
            duration=body.duration,
            materials=_materials(body.materials),
        )
        return {"brief": text}

    @api.post("/brief/script")
    def write_script(body: ScriptRequest):
        """按诉求写口播脚本（每段一个镜头），交给用户修改。不建项目、不收费。"""
        lang = languages.get(body.language)
        llm = registry.create("llm", None, settings)
        title, lines = brief_service.write_script(
            llm,  # type: ignore[arg-type]
            body.brief,
            video_type=body.video_type,
            language=lang.prompt_name,
            aspect=body.aspect,
            duration=body.duration,
            materials=_materials(body.materials),
        )
        return {"title": title, "lines": lines, "seconds": brief_service.script_seconds(lines)}

    @api.post("/projects/{project_id}/storyboard")
    def project_storyboard(project_id: str, body: StoryboardRequest):
        """给项目写分镜：有定稿脚本时按脚本一段一个镜头，只配画面；再把上传的素材放到该放的地方。"""
        project = store.load(_id(project_id))
        materials = _materials(body.materials)
        for item in materials:
            if item.asset_id is not None and _id(item.asset_id) not in project.assets:
                raise HTTPException(404, "素材不存在")
        lang = languages.get(project.language)
        try:
            llm = registry.create("llm", None, settings)
        except ProviderNotConfigured:
            if not body.script:
                raise
            llm = None
        if llm is None:
            # 没配大模型、但脚本是自己写好的：一段一个镜头，画面先用上传的图片、视频或纯色底，不花钱也能出片。
            board = storyboard.plain_storyboard(body.script or [], title=project.title)
        else:
            board = storyboard.write_storyboard(
                llm,  # type: ignore[arg-type]
                body.subject.strip(),
                language=lang.prompt_name,
                prompt_lang=prompting.prompt_language(project),
                duration=body.duration,
                video_type=project.video_type,
                aspect=project.canvas.aspect,
                script=body.script,
                materials=brief_service.describe_materials(materials),
            )

        def change(latest: Project) -> None:
            latest.subject = body.subject.strip()
            if body.idea.strip():
                latest.idea = body.idea.strip()
            latest.script = [shot.text for shot in board.shots if shot.text.strip()][:60]
            if body.duration:
                latest.target_seconds = body.duration
            if not latest.title or latest.title == "未命名项目":
                latest.title = board.title or body.subject[:40]
            latest.shots = board.shots
            latest.look = board.look
            latest.look.confirmed = False  # 新写的主体要先准备、确认，才能生成 AI 画面
            if latest.video_type and llm is not None:
                storyboard.suggest_sources(latest.shots, visuals.available_sources(settings), latest.look)
            brief_service.apply_materials(latest, materials, board.footage, photos_as_footage=llm is None)

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/materials")
    def apply_materials(project_id: str, body: ApplyMaterials):
        """把新建时上传的素材放到该放的地方（不调用大模型）。"""

        def change(latest: Project) -> None:
            materials = _materials(body.materials)
            for item in materials:
                if item.asset_id is None or item.asset_id not in latest.assets:
                    raise KeyError(item.asset_id)
            brief_service.apply_materials(latest, materials)

        return _mutate(project_id, change)

    @api.get("/projects/{project_id}")
    def get_project(project_id: str):
        return store.load(_id(project_id))

    @api.patch("/projects/{project_id}")
    def update_project(project_id: str, body: UpdateProject):
        def change(project: Project) -> None:
            if body.language is not None:
                if body.language not in languages.LANGUAGES:
                    raise ValueError("不支持的语言")
                localize.apply_language(project, body.language)
            for name in (
                "title",
                "subject",
                "idea",
                "script",
                "video_type",
                "titles",
                "chain_frames",
                "target_seconds",
                "canvas",
                "voice",
                "pronunciations",
                "subtitle",
                "overlays",
                "transition",
                "bgm",
                "sfx",
                "delivery",
                "references",
                "ai_badge",
            ):
                value = getattr(body, name)
                if value is not None:
                    setattr(project, name, value)
            if body.look is not None:
                project.look = readiness.carry_over(project.look, body.look)
            if "loudness" in body.model_fields_set:
                project.loudness = body.loudness
            if "budget" in body.model_fields_set:
                project.budget = body.budget
            if project.sfx.ambience and (
                project.sfx.ambience not in project.assets
                or project.assets[project.sfx.ambience].kind != "audio"
            ):
                raise ValueError("环境声必须是项目里的音频素材")
            if project.bgm.asset_id and project.bgm.asset_id not in project.assets:
                raise ValueError("背景音乐素材不存在")
            for item in project.look.subjects:
                for image in item.images:
                    asset = project.assets.get(image)
                    if asset is None:
                        raise ValueError("全片设定引用的参考素材不存在")
                    if asset.kind != "image":
                        raise ValueError("参考素材必须是图片")
            for ref in project.references:
                asset = project.assets.get(ref.asset_id)
                if asset is None:
                    raise ValueError("参考素材不存在")
                if asset.kind != "image":
                    raise ValueError("参考素材必须是图片")

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/look/{code}/image")
    def subject_image(project_id: str, code: str, body: ChargeConsent | None = None):
        """给全片设定里的主体生成定妆图（AI 图片，收费，需要 confirm_charge）。"""
        project = store.load(_id(project_id))
        if not any(s.code == code for s in project.look.subjects):
            raise HTTPException(404, "主体不存在")
        asset_id = visuals.generate_subject_image(
            store, settings, project, code, confirm_charge=(body or ChargeConsent()).confirm_charge
        )

        def change(latest: Project) -> None:
            latest.assets.setdefault(asset_id, project.assets[asset_id])
            latest.cost_credits = max(latest.cost_credits, project.cost_credits)
            subject = next((s for s in latest.look.subjects if s.code == code), None)
            if subject is None:
                raise KeyError(code)
            if asset_id not in subject.images:
                subject.images.append(asset_id)

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/look/confirm")
    def confirm_look(project_id: str):
        """确认素材（全片设定）已准备好，之后才能生成 AI 画面。没准备好时返回 409 并说明缺什么。"""

        def change(project: Project) -> None:
            try:
                readiness.confirm(project)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/look/{code}/describe")
    def describe_subject(project_id: str, code: str):
        """看主体的参考图写外观描述（领型、有无帽子、拉链、缝线、Logo 位置），只返回文字，不保存。不收费。"""
        project = store.load(_id(project_id))
        try:
            text = inspect_service.describe_subject(settings, store, project, code)
        except KeyError:
            raise HTTPException(404, "主体不存在") from None
        return {"appearance": text}

    @api.post("/projects/{project_id}/pronunciations/suggest")
    def suggest_pronunciations(project_id: str):
        """AI 检查文案里容易读错的词（多音字等），追加到读音纠正表。不收费。"""
        project = store.load(_id(project_id))
        llm = registry.create("llm", None, settings)
        found = pronounce.suggest(llm, project)  # type: ignore[arg-type]

        def change(latest: Project) -> None:
            known = {item.word for item in latest.pronunciations}
            latest.pronunciations += [item for item in found if item.word not in known]

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/voice/preview")
    def preview_voice(project_id: str, body: PreviewVoice):
        """按当前配音设置和读音纠正合成一句试听。"""
        project = store.load(_id(project_id))
        try:
            path = voice_stage.preview_voice(store, settings, project, body.text)
        except RenderError as exc:
            raise HTTPException(422, str(exc)) from None
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from None
        media_type = (
            "audio/mpeg" if path.suffix == ".mp3" else "audio/mp4" if path.suffix == ".m4a" else "audio/wav"
        )
        return FileResponse(path, media_type=media_type)

    @api.post("/projects/{project_id}/subtitle/keywords")
    def suggest_keywords(project_id: str):
        """AI 挑选字幕高亮关键词，替换 subtitle.keywords。不生成画面、不收费。"""
        project = store.load(_id(project_id))
        llm = registry.create("llm", None, settings)
        words = titles_service.suggest_keywords(llm, project)  # type: ignore[arg-type]

        def change(latest: Project) -> None:
            latest.subtitle.keywords = words

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/titles/suggest")
    def suggest_titles(project_id: str, body: SuggestTitles | None = None):
        """AI 生成标题：候选大标题存进 titles.candidates，可选地为镜头写小标题。不生成画面、不收费。"""
        body = body or SuggestTitles()
        project = store.load(_id(project_id))
        llm = registry.create("llm", None, settings)
        headlines, subtitle, shot_titles = titles_service.suggest_titles(llm, project)  # type: ignore[arg-type]

        def change(latest: Project) -> None:
            latest.titles.candidates = headlines
            if subtitle and not latest.titles.intro.subtitle:
                latest.titles.intro.subtitle = subtitle
            if body.apply_headline:
                latest.titles.headline.text = headlines[0]
                latest.titles.headline.enabled = True
            if body.shot_titles:
                ids = [shot.id for shot in project.shots]
                for index, title in shot_titles.items():
                    try:
                        target = latest.shot(ids[index - 1])
                    except (KeyError, IndexError):
                        continue
                    if not target.title.strip():
                        target.title = title

        return _mutate(project_id, change)

    @api.post("/projects/{project_id}/duplicate", status_code=201)
    def duplicate(project_id: str, body: DuplicateProject | None = None):
        """把已有项目复制成新项目（素材、设置、镜头和渲染缓存一起复制），返回新项目。"""
        with store.lock(_id(project_id)):
            project = store.load(project_id)
            return duplicate_project(store, project, title=body.title if body else None)

    @api.get("/projects/{project_id}/versions")
    def list_versions(project_id: str):
        """同一个视频的所有语言版本（包括自己）。"""
        project = store.load(_id(project_id))
        return [
            {
                "id": p.id,
                "title": p.title,
                "language": languages.normalize(p.language),
                "updated_at": p.updated_at,
            }
            for p in localize.versions(store, project)
        ]

    @api.post("/projects/{project_id}/versions", status_code=201)
    def create_version(project_id: str, body: CreateVersion):
        """调用大模型把文案翻译成另一种语言，生成一个新项目（画面素材共用）。"""
        project = store.load(_id(project_id))
        if body.language not in languages.LANGUAGES:
            raise HTTPException(422, "不支持的语言")
        llm = registry.create("llm", None, settings)
        return localize.create_language_version(store, llm, project, body.language)  # type: ignore[arg-type]

    @api.delete("/projects/{project_id}", status_code=204)
    def delete_project(project_id: str, force: bool = False):
        pid = _id(project_id)
        active = [j for j in jobs.list(pid) if j.state in ("queued", "running")]
        if active and not force:
            # 渲染中途删目录会让任务写文件时抛难以理解的错误。
            raise HTTPException(409, "这个项目正在渲染，请等它结束，或加 ?force=true 强制删除")
        for job in active:
            jobs.cancel(job.id)
        store.delete(pid)

    # ---- 费用预估 ------------------------------------------------------------

    @api.get("/projects/{project_id}/cost-estimate")
    def get_cost_estimate(project_id: str, quality: Literal["draft", "final"] = "final"):
        """估算渲染本项目的费用（元），不实际触发任何 AI 调用。草稿档按草稿分辨率估算。"""
        project = store.load(_id(project_id))
        return cost_service.estimate_render_cost(project, settings, quality).to_dict()

    @api.get("/projects/{project_id}/checkup")
    def get_checkup(project_id: str, quality: Literal["draft", "final"] = "final"):
        """渲染前体检：列出缺画面、素材太短、分辨率不足、文字重叠、超出费用上限等问题。只读。"""
        project = store.load(_id(project_id))
        return {"issues": checkup.to_dicts(checkup.check_project(project, settings, quality=quality))}
