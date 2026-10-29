"""设置页：在界面里改 config.toml、测试服务连接。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from shotwright import config_edit
from shotwright.api.context import ApiContext
from shotwright.api.schemas import AppSettings, DefaultProvider, ProviderValues
from shotwright.config import PROVIDER_KINDS
from shotwright.providers import registry
from shotwright.providers.base import ProviderError


def register(api: APIRouter, ctx: ApiContext) -> None:
    settings = ctx.settings

    # ---- 设置：在界面里改 config.toml --------------------------------------------

    def _mask(value: str) -> str:
        return "••••" + value[-4:] if len(value) > 8 else "••••"

    def _config_path() -> Path:
        if settings.config_path is None:
            raise HTTPException(409, "当前没有配置文件路径，不能在界面里修改配置")
        return settings.config_path

    def _spec_or_404(kind: str, provider_id: str):
        try:
            return registry.spec(kind, provider_id)
        except ProviderError:
            raise HTTPException(404, "服务不存在") from None

    @api.get("/settings")
    def read_settings():
        """设置页：当前的默认服务、代理和各服务填写的值（密钥只返回是否已填和后 4 位）。"""
        result: dict[str, dict] = {}
        for spec in registry.specs():
            saved = settings.provider_settings(spec.kind, spec.id)
            fields = {}
            for f in spec.fields:
                value = str(saved.get(f.key, ""))
                fields[f.key] = {"set": bool(value), "value": _mask(value) if f.secret and value else value}
            result.setdefault(spec.kind, {})[spec.id] = {
                "configured": registry.is_configured(spec, settings),
                "fields": fields,
            }
        return {
            "config_path": str(settings.config_path) if settings.config_path else "",
            "editable": settings.config_path is not None,
            "proxy": settings.proxy or "",
            "defaults": {kind: settings.default_provider(kind) for kind in PROVIDER_KINDS},
            "providers": result,
        }

    @api.put("/settings/providers/{kind}/{provider_id}")
    def save_provider_settings(kind: str, provider_id: str, body: ProviderValues):
        """保存一个服务的配置到 config.toml，立即生效（不用重启）。"""
        spec = _spec_or_404(kind, provider_id)
        known = {f.key for f in spec.fields}
        unknown = set(body.values) - known
        if unknown:
            raise HTTPException(422, f"不认识的配置项：{', '.join(sorted(unknown))}")
        changes: dict[str, str | None] = {key: (value.strip() or None) for key, value in body.values.items()}
        try:
            config_edit.update(_config_path(), ["providers", kind, provider_id], changes)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        current = settings.providers.setdefault(kind, {}).setdefault(provider_id, {})
        for key, value in changes.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        return read_settings()

    @api.put("/settings/defaults")
    def save_default_provider(body: DefaultProvider):
        _spec_or_404(body.kind, body.provider)
        try:
            config_edit.update(_config_path(), ["defaults"], {body.kind: body.provider})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        settings.defaults[body.kind] = body.provider
        return read_settings()

    @api.put("/settings/app")
    def save_app_settings(body: AppSettings):
        proxy = body.proxy.strip()
        if proxy and not proxy.startswith(("http://", "https://", "socks5://", "socks5h://")):
            raise HTTPException(422, "代理地址要以 http://、https:// 或 socks5:// 开头")
        try:
            config_edit.update(_config_path(), ["app"], {"proxy": proxy or None})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        settings.proxy = proxy or None
        return read_settings()

    @api.post("/settings/providers/{kind}/{provider_id}/test")
    def test_provider(kind: str, provider_id: str):
        """测试连接：发一个不花钱（或几乎不花钱）的请求，确认 Key、地址、模型名对不对。"""
        _spec_or_404(kind, provider_id)
        try:
            provider = registry.create(kind, provider_id, settings)
        except ProviderError as exc:
            return {"ok": False, "message": str(exc)}
        check = getattr(provider, "check", None)
        if check is None:
            return {"ok": False, "message": "这个服务暂不支持测试连接"}
        ok, message = check()
        return {"ok": ok, "message": message}
