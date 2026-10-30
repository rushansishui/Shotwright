"""命令行入口：``shotwright <命令>``。

shotwright serve                         启动 API 和网页界面
shotwright new --script-file a.txt       按句子把文案拆成镜头，新建项目
shotwright new --subject "主题" --llm    让大模型写文案并拆镜头（--language en-US 生成英文视频）
shotwright translate <项目> en-US        生成英文版本（新项目，画面共用）
shotwright list                          列出项目
shotwright show <项目>                   查看镜头
shotwright shot <项目> <序号> --text …   修改某个镜头
shotwright render <项目> [--allow-paid]  渲染（只重做有改动的镜头）
shotwright copy <项目> [--title 标题]    复制成新项目继续编辑
shotwright bench [--render]              跑质量基准集，结果存档并与上次对比
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from shotwright import __version__
from shotwright.config import load_settings
from shotwright.core import languages
from shotwright.core.models import Canvas, Project, VoiceSettings
from shotwright.core.store import ProjectStore
from shotwright.logs import setup_logging
from shotwright.media import ffmpeg
from shotwright.pipeline.render import Renderer
from shotwright.providers import registry
from shotwright.services import localize, prompting, storyboard, visuals


def _store(args) -> tuple:
    settings = load_settings(args.config)
    setup_logging(settings, console=False)
    ffmpeg.configure(settings.ffmpeg, settings.ffprobe)
    store = ProjectStore(settings.storage_dir)
    visuals.organize_all(store)
    return settings, store


PORT_TRIES = 50
"""首选端口用不了时，往后再试这么多个端口。"""


def bind_port(host: str, port: int, tries: int = PORT_TRIES):
    """绑定首选端口；被占用或被系统保留（Windows 的 WinError 10013）时依次试后面的端口。

    返回已经绑定好的 socket，直接交给 uvicorn，避免「先探测、再绑定」之间被别的程序抢走。
    都不行时让系统分配一个空闲端口。
    """
    import socket

    candidates = [port + offset for offset in range(tries + 1) if port + offset < 65536] + [0]
    last_error: OSError | None = None
    for candidate in candidates:
        sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == "win32":
            # 独占绑定：Windows 上默认允许别的程序抢同一个端口，独占后被占用时会直接报错。
            sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, candidate))
        except OSError as exc:
            sock.close()
            last_error = exc
            continue
        sock.listen(2048)
        sock.set_inheritable(True)
        return sock
    raise OSError(f"找不到可用的端口：{last_error}")


def cmd_serve(args) -> int:
    import threading
    import webbrowser

    import uvicorn

    from shotwright.api.app import create_app

    app = create_app(load_settings(args.config))
    try:
        sock = bind_port(args.host, args.port)
    except OSError as exc:
        print(f"Shotwright 启动失败：{exc}")
        return 1
    port = sock.getsockname()[1]
    if port != args.port:
        print(f"端口 {args.port} 无法使用（被其他程序占用或被系统保留），改用端口 {port}")
    url = f"http://{args.host}:{port}"
    print(f"Shotwright 已启动：{url}")
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=port))
    from shotwright.logs import quiet_polling_access_log

    quiet_polling_access_log()  # uvicorn 配置日志之后再挂一次过滤，避免被覆盖
    if args.open:

        def open_when_ready() -> None:
            for _ in range(300):
                if server.started:
                    webbrowser.open(url)
                    return
                threading.Event().wait(0.1)

        threading.Thread(target=open_when_ready, daemon=True).start()
    server.run(sockets=[sock])
    return 0


def cmd_new(args) -> int:
    settings, store = _store(args)
    project = Project(
        subject=args.subject or "",
        canvas=Canvas(aspect=args.aspect),
        target_seconds=args.duration,
        video_type=args.type,
    )
    tts = args.tts or settings.default_provider("tts") or "edge"
    project.voice = VoiceSettings(provider=tts)
    localize.apply_language(project, args.language)
    if args.voice:
        project.voice = project.voice.model_copy(update={"voice": args.voice})
    if args.llm:
        if not args.subject:
            print("使用 --llm 时需要 --subject", file=sys.stderr)
            return 2
        board = storyboard.write_storyboard(
            registry.create("llm", None, settings),  # type: ignore[arg-type]
            args.subject,
            language=languages.get(args.language).prompt_name,
            prompt_lang=prompting.prompt_language(project),
            shots=args.shots,
            duration=args.duration,
            video_type=args.type,
            aspect=args.aspect,
        )
        project.title, project.shots, project.look = board.title, board.shots, board.look
        if args.type:
            storyboard.suggest_sources(project.shots, visuals.available_sources(settings), project.look)
        for issue in board.issues:
            print(f"分镜自检：{issue}", file=sys.stderr)
    else:
        script = Path(args.script_file).read_text(encoding="utf-8") if args.script_file else args.script
        if not script:
            print("请提供 --script、--script-file，或使用 --llm", file=sys.stderr)
            return 2
        project.shots = storyboard.split_script(script)
        project.title = args.subject or project.shots[0].text[:20]
    store.create(project)
    print(project.id)
    _print_shots(project)
    return 0


def _print_shots(project: Project) -> None:
    print(f"《{project.title}》 {project.canvas.aspect}  {len(project.shots)} 个镜头")
    for index, shot in enumerate(project.shots, start=1):
        visual = shot.visual
        source = visual.mode + ("（已锁定）" if visual.locked else "")
        terms = ", ".join(visual.terms)
        print(f"  {index:>2}. [{source}] {shot.text}")
        if terms:
            print(f"      检索词：{terms}")


def cmd_list(args) -> int:
    _, store = _store(args)
    for project in store.list():
        print(f"{project.id}  {project.canvas.aspect:>5}  {len(project.shots):>2} 个镜头  {project.title}")
    return 0


def cmd_show(args) -> int:
    _, store = _store(args)
    _print_shots(store.load(args.project))
    return 0


def cmd_shot(args) -> int:
    _, store = _store(args)
    with store.lock(args.project):
        project = store.load(args.project)
        if not 1 <= args.index <= len(project.shots):
            print(f"镜头序号应在 1–{len(project.shots)} 之间", file=sys.stderr)
            return 2
        shot = project.shots[args.index - 1]
        if args.text is not None:
            shot.text = args.text
        if args.terms is not None:
            shot.visual.terms = [t.strip() for t in args.terms.split(",") if t.strip()]
        if args.mode:
            visuals.switch_mode(project, shot, args.mode)
        if args.lock:
            shot.visual.locked = True
        if args.unlock:
            shot.visual.locked = False
        store.save(project)
    _print_shots(project)
    return 0


def cmd_render(args) -> int:
    settings, store = _store(args)
    if args.offline:
        settings.auto_fetch_visuals = False

    def progress(value: float, message: str) -> None:
        print(f"\r[{value * 100:5.1f}%] {message:<24}", end="", flush=True)

    result = Renderer(store, settings, progress=progress, allow_paid=args.allow_paid).render(args.project)
    print()
    reused = sum(1 for s in result.stages if s.reused)
    print(f"完成：{result.duration:.1f} 秒，{len(result.stages)} 个阶段中复用了 {reused} 个")
    for warning in result.warnings:
        print(f"提示：{warning}")
    print(store.render_dir(result.project_id, result.render_id) / result.video)
    return 0


def cmd_translate(args) -> int:
    settings, store = _store(args)
    project = store.load(args.project)
    version = localize.create_language_version(
        store,
        registry.create("llm", None, settings),  # type: ignore[arg-type]
        project,
        args.language,
    )
    print(f"已生成{languages.get(version.language).name_zh}版本：{version.id}  {version.title}")
    return 0


def cmd_copy(args) -> int:
    from shotwright.services.duplicate import duplicate_project

    _settings, store = _store(args)
    copy = duplicate_project(store, store.load(args.project), title=args.title)
    print(f"已复制为新项目：{copy.id}  {copy.title}")
    return 0


def cmd_bench(args) -> int:
    import json
    import time

    from shotwright.services import bench

    settings, store = _store(args)
    if args.offline:
        settings.auto_fetch_visuals = False
    root = Path(args.out) if args.out else settings.storage_dir.parent / "bench"
    previous = None
    if args.compare:
        previous = json.loads((Path(args.compare) / "report.json").read_text(encoding="utf-8"))
    else:
        runs = sorted(p for p in root.glob("*") if (p / "report.json").exists()) if root.exists() else []
        if runs:
            previous = json.loads((runs[-1] / "report.json").read_text(encoding="utf-8"))
    out_dir = root / time.strftime("%Y%m%d-%H%M%S")
    report = bench.run_bench(
        store,
        settings,
        out_dir,
        render=args.render,
        allow_paid=args.allow_paid,
        aspects=args.aspect or None,
        only=args.brief or None,
    )
    text = bench.format_report(report.to_dict(), previous)
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"结果已保存到 {out_dir}")
    return 1 if report.summary()["failed"] else 0


def _loads(*modules: str) -> tuple[bool, str]:
    """依次导入这些模块；返回（是否全部成功，说明）。"""
    import importlib
    import importlib.util

    for name in modules:
        if importlib.util.find_spec(name) is None:
            return False, "未安装"
        try:
            importlib.import_module(name)
        except Exception as e:  # noqa: BLE001 - 打包漏带的库可能抛任何错误
            return False, f"导入 {name} 失败：{type(e).__name__}: {e}"
    return True, "可用"


def cmd_doctor(args) -> int:
    """检查运行环境：FFmpeg（含字幕用的 libass）、网页界面、字体、配音和语音识别组件。"""
    import subprocess

    from shotwright import desktop
    from shotwright.config import DEFAULT_FONTS_DIR, ROOT_DIR

    settings = load_settings(args.config)
    results: list[tuple[str, bool, str, bool]] = []  # （项目，是否正常，说明，是否必需）

    def check(name: str, ok: bool, detail: str, required: bool = True) -> None:
        results.append((name, ok, detail, required))

    check("版本", True, __version__)
    check("运行方式", True, "安装包" if desktop.frozen() else "源码")
    check("配置文件", True, str(settings.config_path or "（未使用）"))
    check("数据目录", True, str(Path(settings.storage_dir).resolve()))
    for tool, path in (("ffmpeg", settings.ffmpeg), ("ffprobe", settings.ffprobe)):
        found = shutil.which(path) or (path if Path(path).is_file() else None)
        check(tool, bool(found), found or "找不到，请安装 FFmpeg 或在 config.toml 里填写路径")
    try:
        filters = subprocess.run(
            [settings.ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30
        ).stdout
        check(
            "字幕滤镜 libass",
            " ass " in filters,
            "可用" if " ass " in filters else "这个 FFmpeg 不带 libass，字幕无法烧录",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        check("字幕滤镜 libass", False, str(exc))
    web = ROOT_DIR / "web" / "dist" / "index.html"
    check(
        "网页界面",
        web.is_file(),
        str(web.parent) if web.is_file() else "没有构建，请在 web 目录运行 npm run build",
    )
    fonts = list(DEFAULT_FONTS_DIR.glob("*.[ot]tf")) if DEFAULT_FONTS_DIR.is_dir() else []
    check("内置字体", bool(fonts), f"{len(fonts)} 个" if fonts else f"找不到 {DEFAULT_FONTS_DIR}")
    tts_ok, tts_detail = _loads("edge_tts")
    check("免费配音 edge-tts", tts_ok, tts_detail)
    # 真正导入一遍：打包后漏带的 DLL / 数据文件只有导入时才会暴露。
    asr_ok, asr_detail = _loads("faster_whisper", "ctranslate2", "onnxruntime")
    if not asr_ok and asr_detail == "未安装":
        asr_detail = '未安装（剪辑页需要：pip install -e ".[asr]"）'
    check("本地语音识别", asr_ok, asr_detail, args.strict)

    failed = False
    for name, ok, detail, required in results:
        mark = "OK  " if ok else ("FAIL" if required else "WARN")
        failed = failed or (not ok and required)
        print(f"[{mark}] {name}：{detail}")
    print("\n环境检查未通过" if failed else "\n环境检查通过")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shotwright", description="Shotwright 镜匠：以分镜为核心的短视频生成工具"
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", help="配置文件路径（默认 ./config.toml）")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="启动 API 和网页界面")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000, help="首选端口；用不了时自动换后面的端口")
    serve.add_argument("--open", action="store_true", help="启动后用浏览器打开界面")
    serve.set_defaults(func=cmd_serve)

    doctor = sub.add_parser("doctor", help="检查运行环境（FFmpeg、网页界面、字体、语音识别）")
    doctor.add_argument("--strict", action="store_true", help="可选组件（本地语音识别）缺失也算失败")
    doctor.set_defaults(func=cmd_doctor)

    new = sub.add_parser("new", help="新建项目")
    new.add_argument("--subject", help="视频诉求：想要什么内容（最多 4096 字节）")
    new.add_argument("--script", help="视频文案")
    new.add_argument("--script-file", help="从文件读取文案（UTF-8）")
    new.add_argument("--llm", action="store_true", help="让大模型根据主题写文案并拆镜头")
    new.add_argument("--shots", type=int, default=None, help="期望的镜头数量；默认由大模型决定")
    new.add_argument(
        "--type",
        choices=["product_ad", "brand", "explainer", "talking", "footage"],
        default=None,
        help="视频类型：商品广告、品牌宣传、知识科普、口播、原片改造；诉求模板见 docs/BRIEF_TEMPLATES.md",
    )
    new.add_argument("--duration", type=float, default=None, help="目标总时长（秒）；默认由大模型决定")
    new.add_argument("--aspect", choices=["9:16", "16:9", "1:1"], default="9:16")
    new.add_argument(
        "--language", choices=list(languages.LANGUAGES), default=languages.DEFAULT_LANGUAGE, help="视频语言"
    )
    new.add_argument("--tts", help="配音服务，例如 edge、silent")
    new.add_argument("--voice", help="配音声音，例如 zh-CN-YunxiNeural")
    new.set_defaults(func=cmd_new)

    sub.add_parser("list", help="列出项目").set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="查看项目的镜头")
    show.add_argument("project")
    show.set_defaults(func=cmd_show)

    shot = sub.add_parser("shot", help="修改某个镜头")
    shot.add_argument("project")
    shot.add_argument("index", type=int, help="镜头序号，从 1 开始")
    shot.add_argument("--text")
    shot.add_argument("--terms", help="英文检索词，用逗号分隔")
    shot.add_argument("--mode", choices=["stock", "ai", "image", "upload", "color"])
    shot.add_argument("--lock", action="store_true")
    shot.add_argument("--unlock", action="store_true")
    shot.set_defaults(func=cmd_shot)

    translate = sub.add_parser("translate", help="把项目翻译成另一种语言的版本（新项目）")
    translate.add_argument("project")
    translate.add_argument("language", choices=list(languages.LANGUAGES))
    translate.set_defaults(func=cmd_translate)

    render = sub.add_parser("render", help="渲染项目")
    render.add_argument("project")
    render.add_argument("--offline", action="store_true", help="不自动检索素材，缺画面的镜头用纯色")
    render.add_argument(
        "--allow-paid",
        action="store_true",
        help="允许为缺画面的 AI 镜头调用收费生成（Seedance、Seedream 等）；默认改用素材检索",
    )
    render.set_defaults(func=cmd_render)

    copy = sub.add_parser("copy", help="把项目复制成新项目（素材、设置、镜头一起复制）")
    copy.add_argument("project")
    copy.add_argument("--title", help="新项目的标题；默认「原标题（副本）」")
    copy.set_defaults(func=cmd_copy)

    bench = sub.add_parser("bench", help="跑质量基准集（固定诉求），结果存档并与上次对比")
    bench.add_argument("--render", action="store_true", help="同时渲染草稿成片，测量响度和慢放定格占比")
    bench.add_argument("--allow-paid", action="store_true", help="渲染时允许为 AI 镜头调用收费生成")
    bench.add_argument("--offline", action="store_true", help="渲染时不检索素材，缺画面的镜头用纯色")
    bench.add_argument("--aspect", action="append", choices=["9:16", "16:9", "1:1"], help="只跑这些画面比例")
    bench.add_argument("--brief", action="append", help="只跑这些诉求（briefs.json 里的 id）")
    bench.add_argument("--out", help="结果目录（默认 storage/bench）")
    bench.add_argument("--compare", help="与这次结果对比（默认与最近一次对比）")
    bench.set_defaults(func=cmd_bench)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - 命令行只展示可读的错误
        print(f"\n错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
