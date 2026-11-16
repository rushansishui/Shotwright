"""分层依赖检查：入口 → 服务 → 渲染引擎 → Provider → 核心，只允许自上而下。

文档里的分层规则如果只写在文档里，很快就会长出向上依赖。这个脚本用 AST 扫
``shotwright/`` 下的 import，发现向上的依赖就报错并给出行号；已知的、有意的例外
写在 :data:`EXCEPTIONS` 里，每条都要写理由。

用法（仓库根目录）::

    python scripts/check_layers.py

CI 在 Lint 步骤里跑它；新增的向上依赖会让 CI 直接失败。
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "shotwright"
PACKAGE_NAME = "shotwright"

RANKS = {"core": 0, "providers": 1, "media": 2, "pipeline": 3, "services": 4}
"""层号越小越底层。核心层不允许依赖任何上层模块。"""

EXCEPTIONS: dict[tuple[str, str], str] = {
    ("pipeline/render.py", "shotwright.services.visuals"): (
        "渲染前补素材：没有画面的镜头在渲染时检索/生成候选，是有意的跨层调用"
    ),
    ("pipeline/render.py", "shotwright.services.inspect"): ("成片抽帧检查是可选的后处理，懒加载避免循环依赖"),
    ("pipeline/voice.py", "shotwright.services.transcribe"): ("原声镜头要读识别结果里的逐词时间，懒加载"),
}
"""（相对文件, 被导入的模块）→ 理由。只登记确实有意的例外，不要拿来消音。"""

_PROVIDER_USES_FFMPEG = (
    "providers/asr/faster_whisper.py",
    "providers/tts/edge.py",
    "providers/tts/http_tts.py",
    "providers/tts/silent.py",
    "providers/video_gen/common.py",
)
for _path in _PROVIDER_USES_FFMPEG:
    EXCEPTIONS[(_path, "shotwright.media.ffmpeg")] = (
        "Provider 直接用 media.ffmpeg 做音频转码/探测（薄封装，没有业务逻辑）"
    )


def _layer_of_module(module: str, package: str = PACKAGE_NAME) -> str | None:
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == package and parts[1] in RANKS:
        return parts[1]
    return None


def _layer_of_path(path: Path, root: Path) -> str | None:
    """文件属于哪一层；入口文件（api/cli/desktop/config*）返回 None，不做限制。"""
    relative = path.relative_to(root)
    if relative.parts and relative.parts[0] in RANKS:
        return relative.parts[0]
    return None


def _imported_modules(node: ast.AST) -> list[str]:
    """一条 import 的候选依赖：先模块本身，再 ``from pkg import a`` 里的 pkg.a。

    两种写法都要看：``from shotwright.services import visuals`` 依赖的是子模块，
    而 ``from shotwright.media.ffmpeg import run_ffmpeg`` 依赖的是模块本身。
    """
    if isinstance(node, ast.ImportFrom):
        if not node.module:
            return []  # 相对导入（from .x import y），模块名要结合包路径才能算，跳过
        candidates = [node.module]
        if node.names and node.names[0].name != "*":
            candidates += [f"{node.module}.{alias.name}" for alias in node.names]
        return candidates
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return []


def scan(
    root: Path = PACKAGE,
    *,
    package: str = PACKAGE_NAME,
    exceptions: Mapping[tuple[str, str], str] = EXCEPTIONS,
) -> tuple[list[str], list[str]]:
    """扫描一个包目录，返回（未登记的向上依赖, 已经失效的例外）。"""
    violations: list[str] = []
    used: set[tuple[str, str]] = set()

    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source_layer = _layer_of_path(path, root)
        if source_layer is None:
            continue
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            modules = _imported_modules(node)
            layered = [(module, _layer_of_module(module, package)) for module in modules]
            layered = [(module, layer) for module, layer in layered if layer is not None]
            if not layered:
                continue
            module, target_layer = layered[0]
            if RANKS[target_layer] <= RANKS[source_layer]:
                continue
            matched = [candidate for candidate in modules if (relative, candidate) in exceptions]
            if matched:
                used.update((relative, candidate) for candidate in matched)
                continue
            violations.append(f"{relative}:{node.lineno}  {source_layer} → {target_layer}  {module}")

    stale = [f"{path} → {module}" for path, module in exceptions if (path, module) not in used]
    return violations, stale


def check() -> tuple[list[str], list[str]]:
    return scan(PACKAGE)


def main() -> int:
    violations, stale = check()
    print("分层依赖检查：" + " < ".join(RANKS) + f"（已登记例外 {len(EXCEPTIONS)} 条）")
    for item in stale:
        print(f"失效的例外（代码里已经不再这样导入，可以删掉）：{item}")
    if not violations:
        print("通过：没有未登记的向上依赖。")
        return 0
    print(f"发现 {len(violations)} 处未登记的向上依赖：")
    for item in violations:
        print(f"  {item}")
    print("\n请把逻辑下移到合适的层，或在 scripts/check_layers.py 的 EXCEPTIONS 里写明理由。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
