"""一条命令做出 Shotwright 的宣传片：建项目、配音、自动录屏、放回镜头、渲染。

用法（仓库根目录）::

    pip install -e ".[promo]" && python -m playwright install chromium   # 只需一次
    python scripts/promo/make_promo.py --demo f8ujye85mn4r --lang zh
    python scripts/promo/make_promo.py --demo f8ujye85mn4r --lang en

``--demo`` 是一个做好的演示项目（用过 AI 生成、有参考图、有另一种语言的版本）。脚本把它
和它的语言版本复制到 ``storage/promo`` 里再操作，原项目不受影响；录屏只看、只点不花钱
的按钮。配音默认用免费的 Edge（需要联网），``--tts silent`` 可以离线试跑整个流程。

成片输出到 ``storage/promo/promo-<语言>.mp4``。分镜和口播在 storyboard.py 里改。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path[:0] = [str(HERE), str(ROOT)]

from recorder import Demo, Recorder, finish  # noqa: E402
from storyboard import SHOTS  # noqa: E402

VOICES = {"zh": "zh-CN-YunxiNeural", "en": "en-US-AndrewNeural"}
LANGUAGES = {"zh": "zh-CN", "en": "en-US"}
NEW_LINE = {"zh": "这几款儿童羽绒服，轻薄又保暖！", "en": "These kids' down jackets are light and warm!"}
"""演示「改一句」时换上的文案。"""


def log(message: str) -> None:
    print(f"[promo] {message}", flush=True)


# ---- API --------------------------------------------------------------------


class Api:
    def __init__(self, base: str):
        self.base = base

    def call(self, method: str, path: str, body: dict | None = None, *, timeout: float = 60):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"{self.base}/api/v2{path}", data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise SystemExit(
                f"{method} {path} 失败：{exc.code} {exc.read().decode(errors='replace')[:300]}"
            ) from None

    def upload(self, project_id: str, path: Path, *, audio: bool = False) -> dict:
        boundary = "----promo" + str(int(time.time() * 1000))
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        query = "?as_audio=true" if audio else ""
        req = urllib.request.Request(
            f"{self.base}/api/v2/projects/{project_id}/assets{query}", data=body, method="POST"
        )
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.load(resp)

    def render(self, project_id: str, quality: str) -> dict:
        job = self.call("POST", f"/projects/{project_id}/renders", {"quality": quality})
        while job["state"] in ("queued", "running"):
            time.sleep(2)
            job = self.call("GET", f"/renders/{job['id']}")
        if job["state"] != "done":
            raise SystemExit(f"渲染失败：{job.get('error') or job}")
        return job


# ---- 准备 -------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def link_tree(source: Path, target: Path) -> None:
    """复制项目目录：能用硬链接就用（不占空间、几乎瞬间完成），不行再复制文件。"""

    def link(src: str, dst: str) -> None:
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)

    shutil.copytree(source, target, copy_function=link)


def find_version(storage: Path, project_id: str) -> str | None:
    """同一个视频的另一种语言版本（语言版本组：group 指向原项目）。"""
    main = json.loads((storage / project_id / "project.json").read_text(encoding="utf-8"))
    group = main.get("group") or project_id
    for path in sorted(storage.glob("*/project.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["id"] != project_id and (data.get("group") or data["id"]) == group:
            if data.get("language") != main.get("language"):
                return data["id"]
    return None


def latest_video(storage: Path, project_id: str) -> Path:
    renders = sorted(
        (storage / project_id / "renders").glob("*/video.mp4"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not renders:
        raise SystemExit(f"项目 {project_id} 还没有渲染过成片")
    return renders[0]


def ensure_web_build() -> None:
    """网页界面的构建产物比源码旧时先重新构建，否则录到的是旧界面（按钮、提示都对不上）。"""
    web = ROOT / "web"
    built = web / "dist" / "index.html"
    newest = max(path.stat().st_mtime for path in (web / "src").rglob("*") if path.is_file())
    if built.is_file() and built.stat().st_mtime >= newest:
        return
    log("网页界面有更新，先重新构建（npm run build）…")
    subprocess.run("npm run build", cwd=web, shell=True, check=True)


def start_server(storage: Path, port: int, tts: str) -> subprocess.Popen:
    env = dict(os.environ, SHOTWRIGHT_STORAGE=str(storage), PYTHONIOENCODING="utf-8")
    if tts == "silent":
        env["SHOTWRIGHT_DEFAULT_TTS"] = "silent"
    log_file = open(storage.parent / "server.log", "w", encoding="utf-8")  # noqa: SIM115 - 进程结束时关闭
    proc = subprocess.Popen(
        [sys.executable, "-m", "shotwright", "serve", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        if proc.poll() is not None:
            raise SystemExit(f"服务启动失败，见 {storage.parent / 'server.log'}")
        try:
            urllib.request.urlopen(f"{base}/api/v2/health", timeout=2)
            return proc
        except OSError:
            time.sleep(1)
    raise SystemExit("服务 60 秒内没有就绪")


def grid_clip(
    left: Path, right: Path, labels: tuple[str, str], seconds: float, out: Path, font: Path
) -> Path:
    """两条成片左右并排（各带语言标签），时长不够就循环。"""
    label = (
        "drawtext=fontfile='{font}':text='{text}':x=(w-tw)/2:y=24:fontsize=40:fontcolor=white:"
        "box=1:boxcolor=black@0.55:boxborderw=14"
    )
    chain = (
        f"[0:v]scale=940:-2,{label.format(font=font.as_posix(), text=labels[0])}[a];"
        f"[1:v]scale=940:-2,{label.format(font=font.as_posix(), text=labels[1])}[b];"
        "[a][b]hstack=inputs=2,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=#0B0F19,fps=30[v]"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-stream_loop",
            "-1",
            "-i",
            str(left),
            "-stream_loop",
            "-1",
            "-i",
            str(right),
            "-filter_complex",
            chain,
            "-map",
            "[v]",
            "-t",
            f"{seconds + 1:.2f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
    )
    return out


# ---- 主流程 -----------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--demo", required=True, help="演示项目 id（任一语言版本都行）")
    parser.add_argument("--lang", choices=("zh", "en"), default="zh", help="宣传片语言（界面、配音、字幕）")
    parser.add_argument("--source", default="storage/projects", help="演示项目所在的数据目录")
    parser.add_argument("--work", default="storage/promo", help="工作目录（每次运行会清空重建）")
    parser.add_argument("--tts", choices=("edge", "silent"), default="edge", help="silent：离线试跑，不出声")
    parser.add_argument("--voice", help="配音声音，默认按语言选")
    parser.add_argument("--quality", choices=("draft", "final"), default="final")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口（调试用）")
    parser.add_argument("--only", help="只录这几个动作（逗号分隔），调试用")
    options = parser.parse_args()

    os.chdir(ROOT)
    source = Path(options.source).resolve()
    work = Path(options.work).resolve()
    storage = work / "projects"
    font = ROOT / "resources" / "fonts" / ("NotoSansSC-Bold.ttf")
    if not font.is_file():
        font = next((ROOT / "resources" / "fonts").glob("*.[ot]tf"))
    # FFmpeg 滤镜里的路径用相对路径：Windows 盘符的冒号在滤镜参数里要转义，相对路径省事。
    font = Path(os.path.relpath(font, ROOT))

    # 1. 复制演示项目和它的另一种语言版本
    demo_id = options.demo
    other_id = find_version(source, demo_id)
    if other_id is None:
        raise SystemExit("演示项目没有另一种语言的版本：先在「标题与文字 → 语言版本」里生成一个")
    language = json.loads((source / demo_id / "project.json").read_text(encoding="utf-8"))["language"]
    if not language.startswith(LANGUAGES[options.lang][:2]):
        demo_id, other_id = other_id, demo_id  # 宣传片用哪种语言，就以那种语言的版本为主
    if storage.exists():
        shutil.rmtree(storage)
    storage.mkdir(parents=True)
    for project_id in (demo_id, other_id):
        link_tree(source / project_id, storage / project_id)
    log(f"演示项目 {demo_id}，另一种语言 {other_id}")

    ensure_web_build()
    port = free_port()
    server = start_server(storage, port, options.tts)
    base = f"http://127.0.0.1:{port}"
    api = Api(base)
    recorder = None
    try:
        # 2. 演示「改一句」用的副本；宣传片项目
        edit_copy = api.call("POST", f"/projects/{demo_id}/duplicate", {})["id"]
        shots = SHOTS[options.lang]
        voice = options.voice or VOICES[options.lang]
        project = api.call(
            "POST",
            "/projects",
            {
                "mode": "empty",
                "title": "Shotwright 宣传片" if options.lang == "zh" else "Shotwright promo",
                "language": LANGUAGES[options.lang],
                "canvas": {"aspect": "16:9"},
                "voice": {"provider": "silent" if options.tts == "silent" else "edge", "voice": voice},
            },
        )
        pid = project["id"]
        for item in shots:
            project = api.call("POST", f"/projects/{pid}/shots", {"text": item.text})
        ids = [s["id"] for s in project["shots"]]
        for item, shot_id in zip(shots, ids, strict=True):
            api.call(
                "PATCH",
                f"/projects/{pid}/shots/{shot_id}",
                {"title": item.title[:60], "visual": {"mode": "color", "color": "#0B1020"}},
            )
        titles = project["titles"]
        # 小标题和字幕都加半透明底：叠在界面录屏上也看得清。
        boxed = {"background": "#000000", "background_opacity": 0.6, "outline": 0.0}
        titles["shot"].update({"size": 56, "position": "top", "margin": 50, "color": "#FFE45C", **boxed})
        subtitle = {**project["subtitle"], **boxed}
        update: dict = {"titles": titles, "subtitle": subtitle, "ai_badge": True}
        demo_project = api.call("GET", f"/projects/{demo_id}")
        bgm = (demo_project.get("bgm") or {}).get("asset_id")
        if bgm and bgm in demo_project["assets"]:
            from shotwright.core.models import Project
            from shotwright.core.store import ProjectStore

            store = ProjectStore(storage)
            music = store.asset_path(Project.model_validate(demo_project), bgm)
            asset = api.upload(pid, music, audio=True)
            update["bgm"] = {**project["bgm"], "asset_id": asset["id"], "volume": 0.12}
        api.call("PATCH", f"/projects/{pid}", update)

        # 3. 先出一版纯色草稿，拿到每个镜头的配音时长
        log("生成配音、量每个镜头的时长…")
        api.render(pid, "draft")
        render = api.call("GET", f"/projects/{pid}/renders")["renders"][0]
        seconds = {t["shot_id"]: t["duration"] for t in render["timings"]}

        # 4. 每个镜头的画面
        clips = work / "clips"
        clips.mkdir(exist_ok=True)
        demo = Demo(base, options.lang, demo_id, other_id, edit_copy, NEW_LINE[options.lang])
        only = set(options.only.split(",")) if options.only else None
        for index, (item, shot_id) in enumerate(zip(shots, ids, strict=True), start=1):
            length = seconds[shot_id]
            out = clips / f"{index:02d}-{item.action or item.kind}.mp4"
            if item.kind == "scene":
                if recorder is None:
                    recorder = Recorder(demo, work / "raw", headless=not options.headed)
                log(f"动画 {index}/{len(shots)}：{item.action}（{length:.1f} 秒）")
                clip = recorder.record_scene(item.action, length, ROOT / font)
                finish(clip, length, out, font=font)
            elif item.kind == "grid":
                names = ("中文", "English") if options.lang == "zh" else ("Chinese", "English")
                left, right = (demo_id, other_id) if options.lang == "zh" else (other_id, demo_id)
                grid_clip(latest_video(storage, left), latest_video(storage, right), names, length, out, font)
            else:
                if only is not None and item.action not in only:
                    continue
                if recorder is None:
                    recorder = Recorder(demo, work / "raw", headless=not options.headed)
                log(f"录屏 {index}/{len(shots)}：{item.action}（{length:.1f} 秒）")
                clip = recorder.record(item.action, length)
                finish(clip, length, out, font=font)
            asset = api.upload(pid, out)
            api.call(
                "PATCH",
                f"/projects/{pid}/shots/{shot_id}",
                {"visual": {"mode": "upload", "selected": asset["id"]}},
            )

        # 5. 成片
        log("渲染成片…")
        api.render(pid, options.quality)
        final = latest_video(storage, pid)
        target = work / f"promo-{options.lang}.mp4"
        shutil.copyfile(final, target)
        log(f"完成：{target}")
    finally:
        if recorder is not None:
            recorder.close()
        server.terminate()
        server.wait(timeout=30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
