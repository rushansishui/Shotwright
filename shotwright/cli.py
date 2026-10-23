"""命令行入口：``shotwright <命令>``。

shotwright serve                         启动 API 和网页界面
shotwright new --script-file a.txt       按句子把文案拆成镜头，新建项目
shotwright new --subject "主题" --llm    让大模型写文案并拆镜头（--language en-US 生成英文视频）
shotwright translate <项目> en-US        生成英文版本（新项目，画面共用）
shotwright list                          列出项目
shotwright show <项目>                   查看镜头
shotwright shot <项目> <序号> --text …   修改某个镜头
shotwright render <项目> [--allow-paid]  渲染（只重做有改动的镜头）
"""

from __future__ import annotations

import argparse
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
from shotwright.services import localize, storyboard, visuals


def _store(args) -> tuple:
    settings = load_settings(args.config)
    setup_logging(settings, console=False)
    ffmpeg.configure(settings.ffmpeg, settings.ffprobe)
    store = ProjectStore(settings.storage_dir)
    visuals.organize_all(store)
    return settings, store


def cmd_serve(args) -> int:
    import uvicorn

    from shotwright.api.app import create_app

    app = create_app(load_settings(args.config))
    print(f"Shotwright 已启动：http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
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
        title, shots = storyboard.generate_storyboard(
            registry.create("llm", None, settings),
            args.subject,
            language=languages.get(args.language).prompt_name,
            shots=args.shots,
            duration=args.duration,
            video_type=args.type,
        )
        project.title, project.shots = title, shots
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shotwright", description="Shotwright 镜匠：以分镜为核心的短视频生成工具"
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", help="配置文件路径（默认 ./config.toml）")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="启动 API 和网页界面")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

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
