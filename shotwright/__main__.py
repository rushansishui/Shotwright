"""``python -m shotwright`` 与 PyInstaller 的入口。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
