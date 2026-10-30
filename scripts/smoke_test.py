"""打包后的冒烟测试：启动程序、打开网页、建一个项目并渲染成片，确认安装包真的能用。

只用标准库，CI（windows-latest）和本机都能直接跑::

    python scripts/smoke_test.py dist/Shotwright/Shotwright.exe

用一个临时的数据目录（``SHOTWRIGHT_HOME``），不碰本机已有的配置和项目；
配音用静音、不自动找素材，所以不需要联网，也不需要任何 Key。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = """[app]
auto_fetch_visuals = false

[defaults]
tts = "silent"
"""
SCRIPT = "第一句：打包测试开始。\n第二句：画面和字幕都要正常。\n第三句：渲染完成就算通过。"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def request(base: str, method: str, path: str, body: dict | None = None, timeout: float = 30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        kind = resp.headers.get("Content-Type", "")
    return json.loads(raw) if "json" in kind else raw


def wait_ready(base: str, proc: subprocess.Popen, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"程序提前退出，退出码 {proc.returncode}")
        try:
            request(base, "GET", "/api/v2/health", timeout=3)
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(1)
    raise SystemExit(f"{seconds:.0f} 秒内服务没有就绪")


def step(name: str) -> None:
    print(f"[smoke] {name}", flush=True)


def run(exe: Path, home: Path, timeout: float) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(CONFIG, encoding="utf-8")
    env = dict(os.environ, SHOTWRIGHT_HOME=str(home), PYTHONIOENCODING="utf-8")

    step("doctor --strict")
    subprocess.run([str(exe), "doctor", "--strict"], env=env, check=True, timeout=120)

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(home / "serve.log", "w", encoding="utf-8")  # noqa: SIM115 - 进程结束后关闭
    proc = subprocess.Popen(
        [str(exe), "serve", "--port", str(port)], env=env, stdout=log, stderr=subprocess.STDOUT
    )
    try:
        step(f"启动服务 {base}")
        wait_ready(base, proc, 90)

        step("网页界面")
        page = request(base, "GET", "/")
        if b'<div id="root"' not in page and b"id=root" not in page:
            raise SystemExit("首页不是网页界面")

        step("新建项目")
        project = request(
            base,
            "POST",
            "/api/v2/projects",
            {
                "title": "打包冒烟测试",
                "script": SCRIPT,
                "mode": "split",
                "voice": {"provider": "silent", "voice": ""},
            },
        )
        pid = project["id"]
        shots = project.get("shots") or []
        if len(shots) < 2:
            raise SystemExit(f"脚本没有拆成镜头：{shots}")

        step(f"渲染（{len(shots)} 个镜头）")
        job = request(base, "POST", f"/api/v2/projects/{pid}/renders", {"quality": "draft"})
        deadline = time.time() + timeout
        while job["state"] in ("queued", "running"):
            if time.time() > deadline:
                raise SystemExit(f"{timeout:.0f} 秒内没有渲染完：{job}")
            time.sleep(2)
            job = request(base, "GET", f"/api/v2/renders/{job['id']}")
        if job["state"] != "done":
            raise SystemExit(f"渲染失败：{job.get('error') or job}")

        video = next((home / "storage" / "projects" / pid / "renders").glob("*/video.mp4"), None)
        if video is None or video.stat().st_size < 10_000:
            raise SystemExit("没有找到渲染出的成片")
        probe = subprocess.run(
            [
                str(exe.parent / "ffmpeg" / ("ffprobe" + exe.suffix))
                if (exe.parent / "ffmpeg").is_dir()
                else "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        duration = float(probe.stdout.strip() or 0)
        if duration < 1:
            raise SystemExit(f"成片时长不对：{duration}")
        step(f"成片 {video.stat().st_size // 1024} KB，{duration:.1f} 秒")
    except BaseException:
        proc.terminate()
        proc.wait(timeout=30)
        log.close()
        print("---- serve.log ----")
        print((home / "serve.log").read_text(encoding="utf-8", errors="replace")[-6000:])
        raise
    proc.terminate()
    proc.wait(timeout=30)
    log.close()
    step("全部通过")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default = Path("dist/Shotwright") / ("Shotwright.exe" if sys.platform == "win32" else "Shotwright")
    parser.add_argument("exe", nargs="?", default=str(default), help="打包出的程序")
    parser.add_argument("--home", help="数据目录（默认用临时目录）")
    parser.add_argument("--timeout", type=float, default=600, help="渲染最长等待秒数")
    options = parser.parse_args()
    exe = Path(options.exe).resolve()
    if not exe.is_file():
        raise SystemExit(f"找不到 {exe}")
    if options.home:
        run(exe, Path(options.home), options.timeout)
    else:
        with tempfile.TemporaryDirectory(prefix="shotwright-smoke-", ignore_cleanup_errors=True) as tmp:
            run(exe, Path(tmp), options.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
