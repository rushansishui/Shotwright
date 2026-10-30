"""打包成安装包后的启动准备（shotwright/desktop.py）和 ``shotwright doctor``。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from shotwright import cli, desktop


def test_prepare_does_nothing_from_source(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("SHOTWRIGHT_HOME", str(tmp_path / "home"))
    cwd = Path.cwd()
    assert desktop.prepare() is None
    assert Path.cwd() == cwd
    assert not (tmp_path / "home").exists()


@pytest.fixture
def frozen_app(monkeypatch, tmp_path):
    """模拟 PyInstaller 单目录产物：install/Shotwright(.exe)、install/_internal、install/ffmpeg。"""
    install = tmp_path / "install"
    bundle = install / "_internal"
    bundle.mkdir(parents=True)
    (bundle / "config.example.toml").write_text("[app]\n", encoding="utf-8")
    ffmpeg = install / "ffmpeg"
    ffmpeg.mkdir()
    (ffmpeg / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")).write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "Shotwright.exe"))
    monkeypatch.setenv("SHOTWRIGHT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.chdir(tmp_path)
    return install


def test_prepare_sets_up_user_data_and_ffmpeg(frozen_app, tmp_path):
    home = desktop.prepare()
    assert home == tmp_path / "home"
    assert Path.cwd() == home
    assert (home / "config.toml").read_text(encoding="utf-8") == "[app]\n"
    assert os.environ["PATH"].split(os.pathsep)[0] == str(frozen_app / "ffmpeg")


def test_prepare_keeps_existing_config(frozen_app, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("# 用户改过的配置\n", encoding="utf-8")
    desktop.prepare()
    assert (home / "config.toml").read_text(encoding="utf-8") == "# 用户改过的配置\n"


def test_default_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("SHOTWRIGHT_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    expected = tmp_path / "Shotwright" if os.name == "nt" else Path.home() / ".shotwright"
    assert desktop.data_dir() == expected


def test_loads_reports_missing_and_broken_modules(monkeypatch):
    assert cli._loads("json") == (True, "可用")
    assert cli._loads("json", "shotwright_no_such_module") == (False, "未安装")

    def boom(name):
        raise OSError("DLL load failed")

    monkeypatch.setattr("importlib.import_module", boom)
    ok, detail = cli._loads("json")
    assert not ok and "DLL load failed" in detail


def test_doctor_prints_report(capsys):
    code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "ffmpeg" in out and "本地语音识别" in out
    # 本机装了 FFmpeg（CI 里也装了）时，非 strict 模式下缺语音识别组件只是警告。
    assert code in (0, 1)
