"""运行配置：读取 config.toml，并允许用环境变量覆盖。

查找顺序：参数 → 环境变量 ``SHOTWRIGHT_CONFIG`` → 当前目录的 ``config.toml``。
文件不存在时使用默认值，因此不配置任何东西也能离线跑通（静音配音 + 纯色画面）。

Provider 字段可以用环境变量覆盖，格式为
``SHOTWRIGHT_<类别>_<ID>_<字段>``，例如 ``SHOTWRIGHT_STOCK_PEXELS_API_KEY``。
"""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FONTS_DIR = ROOT_DIR / "resources" / "fonts"

PROVIDER_KINDS = ("llm", "tts", "stock", "video_gen", "image_gen", "music")

DEFAULT_PROVIDERS = {
    "llm": "doubao",
    "tts": "edge",
    "stock": "pixabay",
    "video_gen": "seedance",
    "image_gen": "seedream",
    "music": "archive",
}


@dataclass
class Settings:
    storage_dir: Path = field(default_factory=lambda: Path("storage/projects"))
    fonts_dir: Path = DEFAULT_FONTS_DIR
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    proxy: str | None = None
    defaults: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PROVIDERS))
    providers: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    auto_fetch_visuals: bool = True
    """渲染时为没有素材的镜头自动检索一次素材；关闭后直接用纯色画面。"""
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    allowed_hosts: list[str] = field(default_factory=list)
    """额外放行的 Host（例如局域网部署时的 192.168.x.x）。本机地址始终放行。"""
    config_path: Path | None = None
    """读取的 config.toml 路径；在设置页修改配置时写回这个文件。为空时不能在界面里改配置。"""

    def provider_settings(self, kind: str, provider_id: str) -> dict[str, str]:
        values = dict(self.providers.get(kind, {}).get(provider_id, {}))
        prefix = f"SHOTWRIGHT_{kind}_{provider_id}_".upper()
        for name, value in os.environ.items():
            if name.upper().startswith(prefix) and value:
                values[name[len(prefix) :].lower()] = value
        return values

    def default_provider(self, kind: str) -> str:
        override = os.environ.get(f"SHOTWRIGHT_DEFAULT_{kind}".upper())
        return override or self.defaults.get(kind, "")


def _resolve_tool(name: str, configured: str | None) -> str:
    if configured:
        return configured
    return shutil.which(name) or name


def load_settings(path: str | Path | None = None) -> Settings:
    candidate = path or os.environ.get("SHOTWRIGHT_CONFIG") or "config.toml"
    data: dict = {}
    config_path = Path(candidate)
    if config_path.is_file():
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    elif path is not None:
        raise FileNotFoundError(config_path)

    app = data.get("app", {})
    settings = Settings()
    settings.config_path = config_path.resolve()
    storage = os.environ.get("SHOTWRIGHT_STORAGE") or app.get("storage_dir")
    if storage:
        settings.storage_dir = Path(storage)
    if app.get("fonts_dir"):
        settings.fonts_dir = Path(app["fonts_dir"])
    settings.ffmpeg = _resolve_tool("ffmpeg", app.get("ffmpeg"))
    settings.ffprobe = _resolve_tool("ffprobe", app.get("ffprobe"))
    settings.proxy = app.get("proxy") or None
    settings.auto_fetch_visuals = bool(app.get("auto_fetch_visuals", True))
    if isinstance(app.get("cors_origins"), list):
        settings.cors_origins = [str(item) for item in app["cors_origins"]]
    if isinstance(app.get("allowed_hosts"), list):
        settings.allowed_hosts = [str(item) for item in app["allowed_hosts"]]

    defaults = data.get("defaults", {})
    for kind in PROVIDER_KINDS:
        if isinstance(defaults.get(kind), str):
            settings.defaults[kind] = defaults[kind]

    providers = data.get("providers", {})
    for kind, entries in providers.items():
        if not isinstance(entries, dict):
            continue
        settings.providers[kind] = {
            provider_id: {key: str(value) for key, value in values.items()}
            for provider_id, values in entries.items()
            if isinstance(values, dict)
        }
    return settings
