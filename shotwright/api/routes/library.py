"""品牌包与主体库：跨项目复用的 Logo、品牌色、字体、样式和产品参考图。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from shotwright.api.context import ApiContext
from shotwright.core.store import ProjectNotFound
from shotwright.services import library as library_service


def register(api: APIRouter, ctx: ApiContext) -> None:
    store = ctx.store
    _id = ctx.id
    _mutate = ctx.mutate
    library = library_service.Library(ctx.settings.storage_dir)

    # ---- 品牌包 -------------------------------------------------------------

    @api.get("/library/brands")
    def list_brands():
        return {"brands": [pack.to_dict() for pack in library.brands()]}

    @api.post("/library/brands", status_code=201)
    def create_brand(body: dict):
        """新建或更新一个品牌包（直接提交字段）。``id`` 不给时新建。"""
        pack = library_service.BrandPack.from_dict(body)
        if not pack.name.strip():
            raise HTTPException(422, "请填写品牌包名称")
        return library.save_brand(pack).to_dict()

    @api.post("/library/brands/{brand_id}/logo", status_code=201)
    def upload_brand_logo(brand_id: str, file: UploadFile = File(...)):
        pack = library.brand(_id(brand_id))
        if pack is None:
            raise HTTPException(404, "品牌包不存在")
        filename = Path(file.filename or "logo.png").name
        with tempfile.TemporaryDirectory(prefix="shotwright-brand-") as temp:
            path = Path(temp) / ("logo" + Path(filename).suffix.lower()[:8])
            with path.open("wb") as handle:
                while chunk := file.file.read(1024 * 1024):
                    if handle.tell() + len(chunk) > 20 * 1024 * 1024:
                        raise HTTPException(413, "Logo 超过 20MB")
                    handle.write(chunk)
            pack.logo = library._add_file(path)  # noqa: SLF001 —— 同一个类的内部复用
        return library.save_brand(pack).to_dict()

    @api.delete("/library/brands/{brand_id}")
    def delete_brand(brand_id: str):
        if not library.delete_brand(_id(brand_id)):
            raise HTTPException(404, "品牌包不存在")
        return {"ok": True}

    @api.post("/projects/{project_id}/library/brands", status_code=201)
    def save_brand_from_project(project_id: str, body: dict):
        """把项目当前的样式存成品牌包。``name`` 是品牌包名称，``logo`` 是当作 Logo 的素材 id。"""
        project = store.load(_id(project_id))
        pack = library.save_brand_from_project(
            store, project, str(body.get("name") or ""), body.get("logo") or None
        )
        return pack.to_dict()

    @api.post("/projects/{project_id}/library/brands/{brand_id}")
    def apply_brand(project_id: str, brand_id: str):
        """把品牌包套用到项目：品牌色、字体、调色、Logo 和片尾引导。只改品牌包里填了的项。"""
        pack = library.brand(_id(brand_id))
        if pack is None:
            raise HTTPException(404, "品牌包不存在")

        def change(project) -> None:
            library.apply_brand(store, project, pack)

        return _mutate(project_id, change)

    # ---- 主体库 -------------------------------------------------------------

    @api.get("/library/subjects")
    def list_subjects():
        return {"subjects": [item.to_dict() for item in library.subjects()]}

    @api.delete("/library/subjects/{subject_id}")
    def delete_subject(subject_id: str):
        if not library.delete_subject(_id(subject_id)):
            raise HTTPException(404, "主体不存在")
        return {"ok": True}

    @api.post("/projects/{project_id}/library/subjects", status_code=201)
    def save_subject_from_project(project_id: str, body: dict):
        """把全片设定里的一个主体（连同参考图）存进主体库。``code`` 是主体代号。"""
        project = store.load(_id(project_id))
        try:
            item = library.save_subject_from_project(
                store, project, str(body.get("code") or ""), str(body.get("name") or "")
            )
        except KeyError:
            raise HTTPException(404, "全片设定里没有这个主体") from None
        return item.to_dict()

    @api.post("/projects/{project_id}/library/subjects/{subject_id}")
    def apply_subject(project_id: str, subject_id: str):
        """把主体库里的主体加进项目全片设定，参考图一起复制进项目。"""
        template = next((item for item in library.subjects() if item.id == subject_id), None)
        if template is None:
            raise HTTPException(404, "主体不存在")
        if not store.exists(_id(project_id)):
            raise ProjectNotFound(project_id)
        result: dict = {}

        def change(project) -> None:
            result["code"] = library.apply_subject(store, project, template)

        _mutate(project_id, change)
        return {"code": result.get("code", template.code)}
