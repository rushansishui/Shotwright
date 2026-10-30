"""Windows 安装包里 Shotwright.exe 的入口（PyInstaller 打包这个文件）。

不能直接打包 ``shotwright/__main__.py``：它用的是相对导入，作为脚本运行时会报错。
双击图标（不带参数）时启动服务并打开浏览器；带参数时和命令行 ``shotwright`` 一样。
"""

import sys
import traceback


def _utf8_pipes() -> None:
    """输出被重定向（CI、日志文件）时 Windows 默认用 ANSI 代码页，打印中文会出错，统一改成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")


def _run(args: list[str]) -> int:
    from shotwright import desktop
    from shotwright.cli import main

    desktop.prepare()
    return main(args or ["serve", "--open"])


if __name__ == "__main__":
    _utf8_pipes()
    args = sys.argv[1:]
    try:
        code = _run(args)
    except KeyboardInterrupt:
        code = 0
    except Exception:  # noqa: BLE001 - 双击启动时把错误留在窗口里，方便用户截图反馈
        traceback.print_exc()
        code = 1
    if code and not args:
        # 双击启动失败时窗口会一闪而过，停一下让用户看到原因。
        input("\nShotwright 没能启动，按回车键关闭窗口… / Press Enter to close…")
    raise SystemExit(code)
