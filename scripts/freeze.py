"""用 PyInstaller 把 Shotwright 冻结成单目录程序（dist/Shotwright/）。

Windows 安装包（scripts/build-installer.ps1）和 CI 都调用这里，打包参数只维护一份。
用法（仓库根目录，已 ``pip install -e ".[asr]" pyinstaller``，并已构建 web/dist）::

    python scripts/freeze.py [--ffmpeg-dir 目录]

``--ffmpeg-dir`` 指向含 ffmpeg / ffprobe 可执行文件的目录时，一起放进 dist/Shotwright/ffmpeg/，
安装后不用另装 FFmpeg。

程序目录里还会放上 LICENSE 和 NOTICE.md（第三方声明）；第三方 Python 库的许可证
（THIRD_PARTY_LICENSES.txt）由 scripts/build-installer.ps1 在冻结后生成。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 输出被重定向时（GitHub Actions、写进日志），Windows 默认用系统的 ANSI 编码；
# 英文系统上是 cp1252，打印中文会直接报 UnicodeEncodeError。统一改成 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 这些包带有数据文件、二进制库或动态导入，需要整包收集。
COLLECT_ALL = (
    "edge_tts",
    "faster_whisper",  # 自带 VAD 模型（assets/*.onnx）
    "ctranslate2",  # 语音识别推理库（含 DLL）
    "onnxruntime",  # VAD 推理
    "tokenizers",
    "av",  # faster-whisper 导入时需要，实际解码用的是 FFmpeg
)
# 运行时用 importlib.metadata 查版本的包，要带上它们的元数据。
COPY_METADATA = ("huggingface_hub", "tqdm", "filelock", "regex", "requests", "packaging", "safetensors")
HIDDEN_IMPORTS = (
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
)


def _installed(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def _has_metadata(name: str) -> bool:
    from importlib import metadata

    try:
        metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return False
    return True


def build_args() -> list[str]:
    sep = os.pathsep  # --add-data 的分隔符：Windows 是 ;，其他系统是 :
    if not (ROOT / "web" / "dist" / "index.html").is_file():
        raise SystemExit("web/dist 不存在：请先在 web 目录运行 npm run build")
    args = [
        "--noconfirm",
        "--clean",
        "--console",  # 本地服务：保留一个窗口显示地址，关掉窗口就停止服务
        "--name",
        "Shotwright",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
        "--specpath",
        str(ROOT / "build"),
        "--paths",
        str(ROOT),
        "--add-data",
        f"{ROOT / 'web' / 'dist'}{sep}web/dist",
        "--add-data",
        f"{ROOT / 'resources'}{sep}resources",
        "--add-data",
        f"{ROOT / 'config.example.toml'}{sep}.",
        "--collect-submodules",
        "shotwright",
    ]
    icon = ROOT / "resources" / "icon.ico"
    if icon.is_file():
        args += ["--icon", str(icon)]
    for name in COLLECT_ALL:
        if _installed(name):
            args += ["--collect-all", name]
        else:
            print(f"[freeze] 跳过没有安装的 {name}")
    for name in COPY_METADATA:
        if _has_metadata(name):
            args += ["--copy-metadata", name]
    for name in HIDDEN_IMPORTS:
        args += ["--hidden-import", name]
    args.append(str(ROOT / "scripts" / "shotwright_launcher.py"))
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-dir", help="含 ffmpeg / ffprobe 的目录，一起打包")
    options = parser.parse_args()

    import PyInstaller.__main__

    PyInstaller.__main__.run(build_args())
    out = ROOT / "dist" / "Shotwright"
    if options.ffmpeg_dir:
        source = Path(options.ffmpeg_dir)
        target = out / "ffmpeg"
        target.mkdir(exist_ok=True)
        suffix = ".exe" if sys.platform == "win32" else ""
        for tool in ("ffmpeg", "ffprobe"):
            path = source / f"{tool}{suffix}"
            if not path.is_file():
                raise SystemExit(f"找不到 {path}")
            shutil.copy2(path, target / path.name)
        # FFmpeg 是 GPL 授权：许可证和写着源码地址的 README 要随程序一起分发。
        for extra in ("LICENSE", "LICENSE.txt", "README.txt"):
            if (source / extra).is_file():
                shutil.copy2(source / extra, target / f"FFmpeg-{extra}")
    # Shotwright 自己的许可证和第三方声明，放在 Shotwright.exe 旁边。
    for name in ("LICENSE", "NOTICE.md"):
        shutil.copy2(ROOT / name, out / name)
    print(f"[freeze] 完成：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
