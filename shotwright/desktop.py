"""打包成桌面程序（Windows 安装包）后运行时的准备工作。

安装后的程序目录可能不可写，卸载时也会被删掉，所以用户数据（配置、项目、日志、导入的字体）
统一放在用户目录：Windows 是 ``%LOCALAPPDATA%\\Shotwright``，其他系统是 ``~/.shotwright``。
第一次启动时把配置模板复制过去，之后都以那里为工作目录，配置里的相对路径（例如
``storage/projects``）也就落在那里。

安装包里自带 FFmpeg（``ffmpeg\\ffmpeg.exe``），启动时放到 PATH 最前面，用户不用另装。

从源码运行（``python -m shotwright``、``start.bat``）时这里什么都不做。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "Shotwright"


def frozen() -> bool:
    """是不是 PyInstaller 打包后的程序。"""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """程序所在目录（Shotwright.exe 旁边）。"""
    return Path(sys.executable).resolve().parent


def bundle_dir() -> Path:
    """打包进去的数据（网页界面、字体、配置模板）所在目录。"""
    return Path(getattr(sys, "_MEIPASS", install_dir()))


def data_dir() -> Path:
    """用户数据目录：配置、项目、日志都在这里，卸载程序时保留。"""
    override = os.environ.get("SHOTWRIGHT_HOME")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    return Path.home() / ".shotwright"


def bundled_ffmpeg() -> Path | None:
    """安装包自带的 FFmpeg 所在目录；没有带时返回 None。"""
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for folder in (install_dir() / "ffmpeg", bundle_dir() / "ffmpeg"):
        if (folder / name).is_file():
            return folder
    return None


def prepare() -> Path | None:
    """打包后的程序启动时调用：准备用户数据目录、配置文件和自带的 FFmpeg。返回数据目录。"""
    if not frozen():
        return None
    home = data_dir()
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.toml"
    if not config.exists():
        template = bundle_dir() / "config.example.toml"
        if template.is_file():
            shutil.copyfile(template, config)
    os.chdir(home)
    ffmpeg = bundled_ffmpeg()
    if ffmpeg is not None:
        os.environ["PATH"] = f"{ffmpeg}{os.pathsep}{os.environ.get('PATH', '')}"
    return home
