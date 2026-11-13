"""桌面窗口：双击打开时界面开在独立窗口里，打不开就退回浏览器；关掉窗口就停止服务。"""

from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path

import pytest

from shotwright import cli, desktop


class FakeServer:
    def __init__(self, start: bool = True):
        self.started = False
        self.should_exit = False
        self._start = start
        self.ran = threading.Event()

    def run(self, sockets=None):
        self.ran.set()
        self.started = self._start
        while not self.should_exit:
            time.sleep(0.01)


def test_window_closes_server(monkeypatch):
    server = FakeServer()
    opened: list[str] = []
    monkeypatch.setattr(desktop, "window_supported", lambda: True)
    monkeypatch.setattr(desktop, "open_window", lambda url, **_: opened.append(url))
    monkeypatch.setattr("webbrowser.open", lambda url: pytest.fail("不该打开浏览器"))
    assert cli._serve_in_window(server, None, "http://127.0.0.1:8000") == 0
    assert opened == ["http://127.0.0.1:8000"]
    assert server.should_exit


def test_window_failure_falls_back_to_browser(monkeypatch):
    server = FakeServer()
    shown: list[bool] = []

    def broken(url, **_):
        raise RuntimeError("WebView2 坏了")

    def browser(url):
        # 用户关掉控制台窗口时服务会停；测试里打开浏览器后直接让服务退出。
        server.should_exit = True

    monkeypatch.setattr(desktop, "window_supported", lambda: True)
    monkeypatch.setattr(desktop, "open_window", broken)
    monkeypatch.setattr(desktop, "show_console", lambda: shown.append(True))
    monkeypatch.setattr("webbrowser.open", browser)
    assert cli._serve_in_window(server, None, "http://127.0.0.1:8000") == 0
    assert shown, "退回浏览器时要把控制台窗口显示出来，用户才知道关掉它会停止服务"


def test_no_webview_uses_browser(monkeypatch):
    server = FakeServer()
    used: list[str] = []
    monkeypatch.setattr(desktop, "window_supported", lambda: False)
    monkeypatch.setattr(desktop, "open_window", lambda url, **_: pytest.fail("不该开窗口"))
    monkeypatch.setattr(desktop, "show_console", lambda: None)

    def browser(url):
        used.append(url)
        server.should_exit = True

    monkeypatch.setattr("webbrowser.open", browser)
    assert cli._serve_in_window(server, None, "http://x") == 0
    assert used == ["http://x"]


def test_server_that_never_starts(monkeypatch):
    server = FakeServer(start=False)
    monkeypatch.setattr(cli, "_wait_started", lambda s, seconds=30.0: False)
    monkeypatch.setattr(desktop, "show_console", lambda: None)
    assert cli._serve_in_window(server, None, "http://x") == 1
    assert server.should_exit


def test_console_helpers_are_noops_off_windows():
    if desktop.os.name == "nt":
        pytest.skip("只在非 Windows 上检查")
    assert desktop.webview2_version() is None
    assert desktop.hide_console() is False
    desktop.show_console()


def test_launcher_double_click_opens_window(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "shotwright_launcher", Path(__file__).resolve().parent.parent / "scripts" / "shotwright_launcher.py"
    )
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    calls: list[list[str]] = []
    monkeypatch.setattr(desktop, "prepare", lambda: None)
    monkeypatch.setattr(desktop, "hide_console", lambda: True)
    monkeypatch.setattr(cli, "main", lambda args: calls.append(args) or 0)
    assert launcher._run([]) == 0
    assert launcher._run(["serve", "--open"]) == 0
    assert calls == [["serve", "--window"], ["serve", "--open"]]


def test_serve_parser_accepts_window():
    args = cli.build_parser().parse_args(["serve", "--window"])
    assert args.window is True
