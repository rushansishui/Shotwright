"""打包成桌面程序（Windows 安装包）后运行时的准备工作。

安装后的程序目录可能不可写，卸载时也会被删掉，所以用户数据（配置、项目、日志、导入的字体）
统一放在用户目录：Windows 是 ``%LOCALAPPDATA%\\Shotwright``，其他系统是 ``~/.shotwright``。
第一次启动时把配置模板复制过去，之后都以那里为工作目录，配置里的相对路径（例如
``storage/projects``）也就落在那里。

安装包里自带 FFmpeg（``ffmpeg\\ffmpeg.exe``），启动时放到 PATH 最前面，用户不用另装。

从源码运行（``python -m shotwright``、``start.bat``）时这里什么都不做。

双击打开时界面显示在独立的桌面窗口里（pywebview + 系统自带的 WebView2，和 Edge 同一个内核），
不再跳到浏览器；关掉窗口就停止服务。没有 WebView2 或窗口打不开时，退回用浏览器打开。
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


# ---- 桌面窗口 -----------------------------------------------------------------

WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
"""WebView2 运行时在 EdgeUpdate 里登记的 id（微软文档给出的检测方法）。"""


def webview2_version() -> str | None:
    """已安装的 WebView2 运行时版本；没装或不是 Windows 时返回 None。

    Windows 11 自带；Windows 10 一般随 Edge 更新装好了。
    """
    if os.name != "nt":
        return None
    import winreg

    places = (
        (
            winreg.HKEY_LOCAL_MACHINE,
            "SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\" + WEBVIEW2_CLIENT,
        ),
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\" + WEBVIEW2_CLIENT),
        (winreg.HKEY_CURRENT_USER, "SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\" + WEBVIEW2_CLIENT),
    )
    for root, key in places:
        try:
            with winreg.OpenKey(root, key) as handle:
                version, _ = winreg.QueryValueEx(handle, "pv")
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return str(version)
    return None


def window_supported() -> bool:
    """能不能用桌面窗口：装了 pywebview，Windows 上还要有 WebView2。"""
    import importlib.util

    if importlib.util.find_spec("webview") is None:
        return False
    if os.name == "nt":
        return webview2_version() is not None
    return True


def _console_window():
    """当前进程的控制台窗口句柄，以及这个控制台是不是只有我们自己在用。"""
    if os.name != "nt":
        return None, False
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetConsoleWindow()
    if not handle:
        return None, False
    processes = (ctypes.c_uint32 * 4)()
    count = kernel32.GetConsoleProcessList(processes, 4)
    return handle, count <= 1


def hide_console() -> bool:
    """双击打开时把黑色的控制台窗口藏起来。

    只在控制台是程序自己的时候藏（双击图标打开）；从命令行运行时控制台是用户的，不动。
    返回是否真的藏了。
    """
    handle, own = _console_window()
    if handle is None or not own:
        return False
    import ctypes

    ctypes.windll.user32.ShowWindow(handle, 0)  # SW_HIDE
    return True


def show_console() -> None:
    """把藏起来的控制台窗口放回来（退回浏览器模式、或启动出错要给用户看原因时）。"""
    handle, own = _console_window()
    if handle is None or not own:
        return
    import ctypes

    ctypes.windll.user32.ShowWindow(handle, 5)  # SW_SHOW


def open_window(url: str, *, title: str = APP_NAME) -> None:
    """在桌面窗口里打开界面，窗口关掉后才返回。打不开时抛出异常，由调用方退回浏览器。"""
    import webview

    settings = getattr(webview, "settings", None)
    if isinstance(settings, dict):
        # 成片、工程包、字幕文件都是用 <a download> 下载的；外部链接（帮助、申请 Key）用系统浏览器打开。
        settings["ALLOW_DOWNLOADS"] = True
        settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.create_window(
        title,
        url,
        width=1440,
        height=920,
        min_size=(1024, 680),
        text_select=True,
        confirm_close=False,
    )
    storage = data_dir() / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    # private_mode=False：保留 localStorage（首页草稿、界面语言、面板展开状态）。
    webview.start(
        gui="edgechromium" if os.name == "nt" else None,
        private_mode=False,
        storage_path=str(storage),
    )
