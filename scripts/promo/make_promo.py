"""一条命令做出 Shotwright 的宣传片：建项目、配音、自动录屏、放回镜头、渲染。

用法（仓库根目录）::

    pip install -e ".[promo]" && python -m playwright install chromium   # 只需一次
    python scripts/promo/make_promo.py --demo f8ujye85mn4r --lang zh                   # 1–2 分钟宣传片
    python scripts/promo/make_promo.py --demo f8ujye85mn4r --lang en --edition long    # 3–5 分钟功能讲解
    python scripts/promo/make_promo.py --demo f8ujye85mn4r --lang zh --edition vertical

``--demo`` 是一个做好的演示项目（用过 AI 生成、有参考图、有另一种语言的版本）。脚本把它
和它的语言版本复制到 ``storage/promo`` 下的新目录再操作，原项目不受影响；录屏只看、只点不花钱
的按钮。配音默认用免费的 Edge（需要联网），``--tts silent`` 可以离线试跑整个流程。

剪辑页那一段要用一个「按说的话剪」、已经识别过原片的剪辑项目：默认自动找语言相同、
最近改过的那个，也可以用 ``--cut-demo`` 指定。

加 ``--edition vertical`` 生成约 30 秒的 9:16 竖屏宣传片。
成片输出到当次运行目录的 ``promo-<版本>-<语言>.mp4``；功能讲解版同时写出
``chapters-<版本>-<语言>.txt``（视频章节时间轴，贴进 YouTube / B 站的简介）。
分镜和口播在 storyboard.py 里改。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path[:0] = [str(HERE), str(ROOT)]

from recorder import Demo, Recorder, finish  # noqa: E402
from storyboard import EDITIONS  # noqa: E402

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

    def render(self, project_id: str, quality: str, *, timeout: float = 600) -> dict:
        job = self.call("POST", f"/projects/{project_id}/renders", {"quality": quality})
        deadline = time.monotonic() + timeout
        while job["state"] in ("queued", "running"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with suppress(OSError, SystemExit):
                    self.call("POST", f"/renders/{job['id']}/cancel", timeout=2)
                raise SystemExit(f"渲染超时：任务 {job['id']}，请查看本次运行的 server.log")
            time.sleep(min(1, remaining))
            job = self.call("GET", f"/renders/{job['id']}", timeout=min(5, remaining))
        if job["state"] != "done":
            raise SystemExit(f"渲染失败：任务 {job['id']}，{job.get('error') or job['state']}")
        return job["result"]


# ---- 准备 -------------------------------------------------------------------


def copy_demo(source: Path, target: Path) -> None:
    shutil.copytree(source, target)
    path = target / "project.json"
    project = json.loads(path.read_text(encoding="utf-8"))
    for shot in project["shots"]:
        shot.get("visual", {}).pop("pending", None)
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")


def find_version(storage: Path, project_id: str, language: str) -> str | None:
    """同一个视频的另一种语言版本（语言版本组：group 指向原项目）。"""
    main = json.loads((storage / project_id / "project.json").read_text(encoding="utf-8"))
    group = main.get("group") or project_id
    for path in sorted(storage.glob("*/project.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["id"] != project_id and (data.get("group") or data["id"]) == group:
            if data.get("language", "").split("-")[0] == language:
                return data["id"]
    return None


def find_cut_demo(storage: Path, lang: str) -> tuple[str, str] | None:
    """语言相同、识别过原片的剪辑项目里最近改过的那个：（项目 id，原片素材 id）。"""
    found: list[tuple[float, str, str]] = []
    for path in storage.glob("*/project.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("origin") != "cut" or not str(data.get("language", "")).startswith(LANGUAGES[lang][:2]):
            continue
        asset = transcribed_asset(path.parent)
        if asset:
            found.append((data.get("updated_at") or 0, data["id"], asset))
    if not found:
        return None
    _, project_id, asset = max(found)
    return project_id, asset


def transcribed_asset(folder: Path) -> str | None:
    """项目里识别完成的原片素材 id。"""
    for item in sorted((folder / "cache" / "transcripts").glob("*.json")):
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("state") == "done" and data.get("words"):
            return item.stem
    return None


def write_chapters(path: Path, shots, timings: list[dict], ids: list[str], lang: str) -> None:
    """按章节卡在成片里的起点写出视频章节（YouTube / B 站简介里用）。第一条必须从 00:00 开始。"""
    starts = {item["shot_id"]: item["start"] for item in timings}
    lines = [("00:00", "开场" if lang == "zh" else "Intro")]
    for item, shot_id in zip(shots, ids, strict=True):
        if item.action == "chapter" and shot_id in starts:
            seconds = int(starts[shot_id])
            lines.append((f"{seconds // 60:02d}:{seconds % 60:02d}", item.card.partition("|")[2]))
    outro = next((sid for item, sid in zip(shots, ids, strict=True) if item.action == "outro"), None)
    if outro in starts:
        seconds = int(starts[outro])
        lines.append((f"{seconds // 60:02d}:{seconds % 60:02d}", "下载与开源" if lang == "zh" else "Get it"))
    path.write_text("\n".join(f"{stamp} {name}" for stamp, name in lines) + "\n", encoding="utf-8")


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
    inputs = (
        *((web / "src").rglob("*")),
        *web.glob("*.json"),
        *web.glob("vite.config.*"),
        web / "index.html",
    )
    newest = max(path.stat().st_mtime for path in inputs if path.is_file())
    if built.is_file() and built.stat().st_mtime >= newest:
        return
    log("网页界面有更新，先重新构建（npm run build）…")
    subprocess.run("npm run build", cwd=web, shell=True, check=True)


def check_edge(voice: str, work: Path) -> None:
    """开跑前先用 Edge 配一句短话：连不上就早点说清楚，不用等录到一半才失败。"""
    from shotwright.config import load_settings
    from shotwright.providers import registry
    from shotwright.providers.base import ProviderError

    previous = Path.cwd()
    os.chdir(ROOT)  # config.toml 在仓库根目录
    try:
        settings = load_settings()
    finally:
        os.chdir(previous)
    tts = registry.create("tts", "edge", settings)
    try:
        tts.synthesize(
            "你好" if voice.startswith("zh") else "Hello",
            voice=voice,
            rate=1.0,
            volume=1.0,
            output=work / "edge-check.mp3",
        )
    except ProviderError as exc:
        proxy = "已配置代理" if settings.proxy else "未配置代理"
        raise SystemExit(
            f"{exc}\n连不上 Edge 在线配音（{proxy}）。可以：\n"
            "  1. 过一会儿再试（Edge 是免费公共服务，偶尔会超时）；\n"
            '  2. 需要代理时，在 config.toml 的 [app] 里填 proxy = "http://127.0.0.1:7890"（换成你的代理端口）；\n'
            "  3. 先加 --tts silent 离线把流程跑通。"
        ) from exc
    log("Edge 配音连接正常")


def stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def start_server(storage: Path) -> tuple[subprocess.Popen, str]:
    from shotwright.config import load_settings

    settings = load_settings()
    config = storage.parent / "promo-config.toml"
    text = "[app]\nauto_fetch_visuals = false\n"
    if settings.proxy:
        text += f"proxy = {json.dumps(settings.proxy, ensure_ascii=False)}\n"
    config.write_text(text, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("SHOTWRIGHT_")}
    env.update(SHOTWRIGHT_STORAGE=str(storage), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    server_log = storage.parent / "server.log"
    with server_log.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "shotwright", "--config", str(config), "serve", "--port", "0"],
            cwd=ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 60
        while proc.poll() is None and time.monotonic() < deadline:
            ready = re.search(
                r"Shotwright 已启动：(http://127[.]0[.]0[.]1:[0-9]+)",
                server_log.read_text(encoding="utf-8", errors="replace"),
            )
            if ready:
                base = ready[1]
                try:
                    with urllib.request.urlopen(f"{base}/api/v2/health", timeout=1):
                        return proc, base
                except OSError:
                    pass
            time.sleep(0.25)
        raise SystemExit(f"宣传片服务没有就绪，请查看 {server_log}")
    except BaseException:
        stop_server(proc)
        raise
    finally:
        config.unlink()


def grid_clip(
    left: Path,
    right: Path,
    labels: tuple[str, str],
    seconds: float,
    out: Path,
    font: Path,
    *,
    vertical: bool = False,
) -> Path:
    """各自等比缩放到同尺寸的格子，时长不够就循环。"""
    label = (
        "drawtext=fontfile='{font}':text='{text}':x=(w-tw)/2:y=24:fontsize=40:fontcolor=white:"
        "box=1:boxcolor=black@0.55:boxborderw=14"
    )
    width, height = (980, 660) if vertical else (920, 760)
    fit = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"setsar=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=#0B1020,fps=30"
    )
    layout = (
        "vstack=inputs=2,pad=1080:1920:(ow-iw)/2:240"
        if vertical
        else "hstack=inputs=2,pad=1920:1080:(ow-iw)/2:150"
    )
    chain = (
        f"[0:v]{fit},{label.format(font=font.as_posix(), text=labels[0])}[a];"
        f"[1:v]{fit},{label.format(font=font.as_posix(), text=labels[1])}[b];"
        f"[a][b]{layout}:color=#0B1020[v]"
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
    parser.add_argument(
        "--edition",
        choices=tuple(EDITIONS),
        default="short",
        help="short：1–2 分钟宣传片；long：3–5 分钟功能讲解；vertical：约 30 秒竖屏版",
    )
    parser.add_argument("--cut-demo", help="演示剪辑页用的剪辑项目 id（默认自动找识别过原片、语言相同的）")
    parser.add_argument("--source", default="storage/projects", help="演示项目所在的数据目录")
    parser.add_argument("--work", default="storage/promo", help="输出根目录（每次运行创建一个子目录）")
    parser.add_argument(
        "--tts", choices=("edge", "silent"), default="edge", help="silent：离线试跑，不生成旁白"
    )
    parser.add_argument("--voice", help="配音声音，默认按语言选")
    parser.add_argument("--quality", choices=("draft", "final"), default="final")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口（调试用）")
    parser.add_argument("--only", help="只录这几个动作（逗号分隔），调试用")
    options = parser.parse_args()

    os.chdir(ROOT)
    source = Path(options.source).resolve()
    output_root = Path(options.work).resolve()
    if output_root.is_relative_to(source):
        parser.error("--work 不能在 --source 的项目目录中")
    font = ROOT / "resources" / "fonts" / ("NotoSansSC-Bold.ttf")
    if not font.is_file():
        font = next((ROOT / "resources" / "fonts").glob("*.[ot]tf"))
    # FFmpeg 滤镜里的路径用相对路径：Windows 盘符的冒号在滤镜参数里要转义，相对路径省事。
    font = Path(os.path.relpath(font, ROOT))

    # 1. 复制演示项目和它的另一种语言版本
    from shotwright.core.models import validate_id

    try:
        demo_id = validate_id(options.demo)
        if options.cut_demo:
            validate_id(options.cut_demo)
    except ValueError as exc:
        parser.error(str(exc))
    demo_folder = (source / demo_id).resolve()
    if demo_folder.parent != source or not (demo_folder / "project.json").is_file():
        parser.error(f"找不到演示项目：{demo_id}")
    language = json.loads((demo_folder / "project.json").read_text(encoding="utf-8"))["language"]
    primary = options.lang
    secondary = "en" if primary == "zh" else "zh"
    demo_language = language.split("-")[0]
    if demo_language == primary:
        other_id = find_version(source, demo_id, secondary)
    else:
        other_id = demo_id if demo_language == secondary else find_version(source, demo_id, secondary)
        demo_id = find_version(source, demo_id, primary)
    if not demo_id or not other_id:
        parser.error("演示项目需要同组的中文和英文版本")
    output_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{options.edition}-{options.lang}-", dir=output_root))
    log(f"运行目录：{work}")
    ensure_web_build()
    if options.tts == "edge":
        check_edge(options.voice or VOICES[options.lang], work)
    storage = work / "projects"
    storage.mkdir()
    for project_id in (demo_id, other_id):
        copy_demo(source / project_id, storage / project_id)
    log(f"演示项目 {demo_id}，另一种语言 {other_id}")
    shots = EDITIONS[options.edition][options.lang]
    vertical = options.edition == "vertical"
    only = set(options.only.split(",")) if options.only else None
    cut_id = cut_asset = ""
    if any(item.action == "cut" for item in shots):
        if options.cut_demo:
            cut_id = options.cut_demo
            cut_asset = transcribed_asset(source / cut_id) or ""
        else:
            cut_id, cut_asset = find_cut_demo(source, options.lang) or ("", "")
        if cut_id and cut_asset:
            if cut_id not in (demo_id, other_id):
                copy_demo(source / cut_id, storage / cut_id)
            log(f"剪辑页演示项目 {cut_id}（原片 {cut_asset}）")
        else:
            log("没找到识别过原片的剪辑项目，跳过剪辑页那一段（可以用 --cut-demo 指定）")
            shots = [item for item in shots if item.action != "cut"]
            cut_id = cut_asset = ""

    server, base = start_server(storage)
    api = Api(base)
    recorder = None
    try:
        # 2. 演示「改一句」用的副本；宣传片项目
        edit_copy = api.call("POST", f"/projects/{demo_id}/duplicate", {})["id"]
        if any(shot.action == "rerender" for shot in shots) and (only is None or "rerender" in only):
            edit = api.call("GET", f"/projects/{edit_copy}")
            changes = {}
            if edit["bgm"]["beat_sync"]:
                changes["bgm"] = {**edit["bgm"], "beat_sync": False}
            if options.tts == "silent":
                changes["voice"] = {**edit["voice"], "provider": "silent"}
                for shot in edit["shots"]:
                    if shot["voice"]:
                        api.call(
                            "PATCH",
                            f"/projects/{edit_copy}/shots/{shot['id']}",
                            {"voice": {**shot["voice"], "provider": "silent"}},
                        )
            if changes:
                api.call("PATCH", f"/projects/{edit_copy}", changes)
            log("预渲染演示副本，为单镜重渲准备缓存…")
            api.render(edit_copy, "final")
        voice = options.voice or VOICES[options.lang]
        project = api.call(
            "POST",
            "/projects",
            {
                "mode": "empty",
                "title": (
                    ("Shotwright 功能讲解" if options.edition == "long" else "Shotwright 宣传片")
                    if options.lang == "zh"
                    else ("Shotwright walkthrough" if options.edition == "long" else "Shotwright promo")
                ),
                "language": LANGUAGES[options.lang],
                "canvas": {"aspect": "9:16" if vertical else "16:9"},
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
                {
                    "title": item.title[:60],
                    "duration": item.seconds,
                    "visual": {"mode": "color", "color": "#0B1020"},
                },
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
        render = api.render(pid, "draft")
        seconds = {}
        for timing in render["timings"]:
            shot_id = timing["shot_id"]
            length = timing["duration"]
            if vertical and options.tts != "silent" and timing["voice_duration"] + 0.2 > length:
                length = round(timing["voice_duration"] + 0.25, 3)
                api.call("PATCH", f"/projects/{pid}/shots/{shot_id}", {"duration": length})
            seconds[shot_id] = length

        # 4. 每个镜头的画面
        clips = work / "clips"
        clips.mkdir(exist_ok=True)
        demo = Demo(
            base,
            options.lang,
            demo_id,
            other_id,
            edit_copy,
            NEW_LINE[options.lang],
            cut_project=cut_id,
            cut_asset=cut_asset,
        )
        for index, (item, shot_id) in enumerate(zip(shots, ids, strict=True), start=1):
            length = seconds[shot_id]
            out = clips / f"{index:02d}-{item.action or item.kind}.mp4"
            if item.kind == "scene":
                if recorder is None:
                    recorder = Recorder(demo, work / "raw", headless=not options.headed)
                log(f"动画 {index}/{len(shots)}：{item.action}（{length:.1f} 秒）")
                clip = recorder.record_scene(
                    item.action, length, ROOT / font, card=item.card, tag=f"-{index:02d}"
                )
                finish(clip, length, out, font=font, vertical=vertical, crop=item.crop)
            elif item.kind == "grid":
                names = ("中文", "English") if options.lang == "zh" else ("Chinese", "English")
                left, right = (demo_id, other_id) if options.lang == "zh" else (other_id, demo_id)
                grid_clip(
                    latest_video(storage, left),
                    latest_video(storage, right),
                    names,
                    length,
                    out,
                    font,
                    vertical=vertical,
                )
            else:
                if only is not None and item.action not in only:
                    continue
                if recorder is None:
                    recorder = Recorder(demo, work / "raw", headless=not options.headed)
                log(f"录屏 {index}/{len(shots)}：{item.action}（{length:.1f} 秒）")
                clip = recorder.record(item.action, length)
                finish(clip, length, out, font=font, vertical=vertical, crop=item.crop)
            asset = api.upload(pid, out)
            api.call(
                "PATCH",
                f"/projects/{pid}/shots/{shot_id}",
                {"visual": {"mode": "upload", "selected": asset["id"]}},
            )

        # 5. 成片
        log("渲染成片…")
        render = api.render(pid, options.quality)
        final = storage / pid / "renders" / render["render_id"] / render["video"]
        target = work / f"promo-{options.edition}-{options.lang}.mp4"
        partial = target.with_suffix(".tmp")
        shutil.copyfile(final, partial)
        partial.replace(target)
        if any(item.action == "chapter" for item in shots):
            chapters = work / f"chapters-{options.edition}-{options.lang}.txt"
            write_chapters(chapters, shots, render["timings"], ids, options.lang)
            log(f"章节时间轴：{chapters}")
        log(f"完成：{target}")
    finally:
        try:
            if recorder is not None:
                recorder.close()
        finally:
            stop_server(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
