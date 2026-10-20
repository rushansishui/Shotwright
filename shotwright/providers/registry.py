"""Provider 注册表。

显式列出内置模块并惰性导入：启动快、导入顺序确定，单个 Provider 导入失败
（例如缺少可选依赖）不会拖垮整个注册表。设计沿用 MoneyPrinterTurbo 中
``video_providers/registry.py`` 的思路。
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from shotwright.config import Settings
from shotwright.providers.base import (
    Factory,
    ProviderError,
    ProviderNotConfigured,
    ProviderSpec,
)

log = logging.getLogger(__name__)

_BUILTIN_MODULES = (
    "shotwright.providers.llm.openai_compat",
    "shotwright.providers.tts.edge",
    "shotwright.providers.tts.silent",
    "shotwright.providers.stock.pixabay",
    "shotwright.providers.stock.pexels",
    "shotwright.providers.video_gen.seedance",
    "shotwright.providers.image_gen.openai_images",
)


@dataclass(frozen=True)
class _Entry:
    spec: ProviderSpec
    factory: Factory


_ENTRIES: dict[tuple[str, str], _Entry] = {}
_ORDER: list[tuple[str, str]] = []
_LOADED = False


def register(spec: ProviderSpec, factory: Factory) -> None:
    key = (spec.kind, spec.id)
    if key not in _ENTRIES:
        _ORDER.append(key)
    _ENTRIES[key] = _Entry(spec, factory)


def _ensure_builtins() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    for module in _BUILTIN_MODULES:
        try:
            importlib.import_module(module)
        except Exception:
            log.exception("加载 Provider 模块失败：%s", module)


def specs(kind: str | None = None) -> list[ProviderSpec]:
    _ensure_builtins()
    return [_ENTRIES[key].spec for key in _ORDER if kind is None or key[0] == kind]


def spec(kind: str, provider_id: str) -> ProviderSpec:
    _ensure_builtins()
    try:
        return _ENTRIES[(kind, provider_id)].spec
    except KeyError:
        raise ProviderError(f"未知的 {kind} Provider：{provider_id}") from None


def create(kind: str, provider_id: str | None, settings: Settings) -> object:
    """按配置实例化 Provider；``provider_id`` 为空时使用该类别的默认值。"""
    _ensure_builtins()
    chosen = provider_id or settings.default_provider(kind)
    if not chosen:
        raise ProviderNotConfigured(f"没有配置 {kind} 类别的 Provider")
    entry = _ENTRIES.get((kind, chosen))
    if entry is None:
        raise ProviderError(f"未知的 {kind} Provider：{chosen}")
    values = _values(entry.spec, settings)
    if settings.proxy and "proxy" not in values:
        values["proxy"] = settings.proxy
    missing = entry.spec.missing_fields(values)
    if missing:
        raise ProviderNotConfigured(f"{entry.spec.name} 缺少配置：{', '.join(missing)}")
    return entry.factory(values)


def _values(spec_: ProviderSpec, settings: Settings) -> dict[str, str]:
    values = {f.key: f.default for f in spec_.fields if f.default}
    values.update(settings.provider_settings(spec_.kind, spec_.id))
    if not values.get("api_key"):
        for kind, provider_id in spec_.shared_key_from:
            shared = settings.provider_settings(kind, provider_id).get("api_key")
            if shared:
                values["api_key"] = shared
                break
    return values


def is_configured(spec_: ProviderSpec, settings: Settings) -> bool:
    return not spec_.missing_fields(_values(spec_, settings))
