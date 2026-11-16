"""分层依赖检查（scripts/check_layers.py）：真实代码干净，且能抓出新的向上依赖。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _checker():
    spec = importlib.util.spec_from_file_location("check_layers", ROOT / "scripts" / "check_layers.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _package(tmp_path: Path) -> Path:
    """造一个最小的分层包：core 在最底下，services 在最上面。"""
    package = tmp_path / "pkg"
    for name in ("core", "services"):
        (package / name).mkdir(parents=True)
        (package / name / "__init__.py").write_text("", encoding="utf-8")
    (package / "core" / "a.py").write_text("from pkg.services import b\n", encoding="utf-8")
    (package / "services" / "b.py").write_text("from pkg.core import a\n", encoding="utf-8")
    return package


def test_real_package_has_no_unregistered_upward_imports():
    violations, stale = _checker().check()
    assert violations == [], violations
    assert stale == [], stale


def test_scan_catches_an_upward_import(tmp_path):
    violations, stale = _checker().scan(_package(tmp_path), package="pkg", exceptions={})
    assert len(violations) == 1
    assert "core → services" in violations[0]
    assert stale == []


def test_registered_exception_is_allowed(tmp_path):
    exceptions = {("core/a.py", "pkg.services.b"): "测试用"}
    violations, stale = _checker().scan(_package(tmp_path), package="pkg", exceptions=exceptions)
    assert violations == []
    assert stale == []


def test_unused_exception_is_reported_as_stale(tmp_path):
    exceptions = {("core/a.py", "pkg.core.b"): "不会命中"}
    _, stale = _checker().scan(_package(tmp_path), package="pkg", exceptions=exceptions)
    assert len(stale) == 1
