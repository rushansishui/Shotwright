"""用浏览器自动操作 Shotwright 的界面并录屏，每个动作录成一段视频。

录屏用 Playwright 自带的录像：每段开一个新的浏览器上下文（1920×1080），页面里注入一个
模拟鼠标指针和点击光圈，鼠标移动有过程，看起来像人在操作。动作只看、只点不花钱的按钮；
会花钱的按钮（生成视频、出关键帧）只把鼠标移上去，不点。

每段录完后按配音时长裁好：开头去掉页面加载，中间等渲染这类长等待剪掉，整体还长就加速。
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from storyboard import DEMO_BRIEF, DEMO_IDEA, DEMO_SCRIPT

VIEWPORT = {"width": 1280, "height": 720}
"""按 1280×720 的窗口排版和录制，导出时放大到 1920×1080：界面文字在视频里够大。
（Chromium 录屏不跟随像素密度，CSS 放大又会打乱按屏幕高度排版的侧栏，所以录完再放大。）"""
MAX_SPEEDUP = 3.0
"""录得太长时最多加速到几倍；再长就从后面截掉。"""

CURSOR_JS = r"""
(() => {
  const install = () => {
    if (document.getElementById('__promo_cursor')) return;
    const cursor = document.createElement('div');
    cursor.id = '__promo_cursor';
    cursor.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24"><path d="M4 2l14 9-6.2 1.3L15 20l-3 1.4-3.2-7.6L4 18z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(cursor.style, {position: 'fixed', left: '-100px', top: '-100px', zIndex: 2147483647,
      pointerEvents: 'none', transform: 'translate(-3px,-2px)', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.35))'});
    document.body.appendChild(cursor);
    window.addEventListener('mousemove', (e) => { cursor.style.left = e.clientX + 'px'; cursor.style.top = e.clientY + 'px'; }, true);
    window.addEventListener('mousedown', (e) => {
      const ring = document.createElement('div');
      Object.assign(ring.style, {position: 'fixed', left: (e.clientX - 22) + 'px', top: (e.clientY - 22) + 'px', width: '44px',
        height: '44px', borderRadius: '50%', border: '3px solid rgba(225,29,72,.85)', zIndex: 2147483646,
        pointerEvents: 'none', transition: 'transform .45s ease-out, opacity .45s ease-out', transform: 'scale(.4)'});
      document.body.appendChild(ring);
      requestAnimationFrame(() => { ring.style.transform = 'scale(1.3)'; ring.style.opacity = '0'; });
      setTimeout(() => ring.remove(), 600);
    }, true);
  };
  if (document.body) install(); else document.addEventListener('DOMContentLoaded', install);
})();
"""

# 界面上的文字（中英文都认），按钮名改了只改这里。
LABELS = {
    "write_own": r"自己写口播脚本|Write my own script",
    "write_myself": r"跳过，自己写诉求|Skip, I'll write the brief",
    "settings": r"^(服务设置|Settings)$",
    "render": r"生成视频|重新渲染|Render video|Re-render",
    "ai_fix": r"让 AI 按建议改写描述|Let AI rewrite it",
    "keyframes": r"先出 3 张关键帧|3 keyframes first",
    "translate": r"翻译并生成|Translate",
    "normalize": r"整理成诉求|整理成完整诉求|Turn into a brief|Write the brief",
    "write_script": r"写口播脚本|Write the script|Write script",
    "board": r"^(分镜表|Storyboard table)$",
    "prompts": r"^(提示词|Prompts)$",
    "prompt_edit": r"^(改这段|Edit this prompt)$",
    "review": r"^(审片|Review)$",
    "approve": r"^(✓ )?(通过|Approve)",
    "sample": r"先看整片样片|Preview the whole cut",
    "fillers": r"勾掉语气词|Remove filler words",
    "undo": r"^(撤销|Undo)$",
    "find": r"^(查找替换|Find & replace)$",
    "csv": r"导出 CSV|Export CSV",
}


@dataclass
class Demo:
    """录屏要用到的项目。"""

    base: str
    """服务地址，例如 http://127.0.0.1:8770"""
    lang: str
    """zh / en：界面语言和演示脚本的语言。"""
    project: str
    """演示项目（对应语言版本）。"""
    other: str
    """另一种语言的版本，演示「多语言」时打开。"""
    edit_copy: str
    """演示「改一句、只重做这一处」的副本，录屏会改它的文案并渲染。"""
    new_line: str
    """「改一句」时换上的新文案。"""
    fonts_dir: Path = Path("resources/fonts")
    cut_project: str = ""
    """演示「按说的话剪」的剪辑项目（识别过原片），没有时跳过剪辑页那一段。"""
    cut_asset: str = ""
    """剪辑项目里识别过的原片素材 id。"""


@dataclass
class Clip:
    path: Path
    ready: float
    """页面加载完、动作开始的时刻（录像里的秒数）。"""
    end: float
    gaps: list[tuple[float, float]] = field(default_factory=list)
    """要剪掉的长等待（录像里的秒数）。"""
    note: str = ""
    """剪掉等待处叠加的说明，例如「实测 38 秒」。"""


class Session:
    """一段录屏里的操作：带过程的鼠标移动、点击、慢慢滚动。"""

    def __init__(self, page, started: float):
        self.page = page
        self.started = started
        self.x, self.y = 640.0, 420.0
        self.gaps: list[tuple[float, float]] = []
        self.note = ""

    def now(self) -> float:
        return time.monotonic() - self.started

    def pause(self, seconds: float) -> None:
        self.page.wait_for_timeout(int(seconds * 1000))

    def move_to(self, locator, *, steps: int = 28) -> bool:
        """把鼠标移到元素上；元素不在页面上时跳过（界面改版后某个按钮不在了，不让整条宣传片失败）。"""
        try:
            box = locator.bounding_box(timeout=4000)
        except Exception:  # noqa: BLE001 - Playwright 超时
            print(f"[promo]   没找到 {locator}，跳过这一步", flush=True)
            return False
        if box and (box["y"] < 70 or box["y"] + min(box["height"], 80) > VIEWPORT["height"] - 20):
            # 页面整体放大后 Playwright 自己的滚动判断不准，交给浏览器平滑滚到中间。
            locator.evaluate("e => e.scrollIntoView({behavior: 'smooth', block: 'center'})")
            self.pause(0.9)
            box = locator.bounding_box()
        if not box:
            return False
        x, y = box["x"] + box["width"] / 2, box["y"] + min(box["height"] / 2, 40)
        self.page.mouse.move(x, y, steps=steps)
        self.x, self.y = x, y
        return True

    def click(self, locator) -> None:
        if not self.move_to(locator):
            return
        self.pause(0.25)
        self.page.mouse.down()
        self.page.mouse.up()
        self.pause(0.35)

    def hover(self, locator, seconds: float = 0.9) -> None:
        if self.move_to(locator):
            self.pause(seconds)

    def scroll(self, pixels: int, *, seconds: float = 1.2) -> None:
        """慢慢滚动：分成几十小步，录出来是平滑的。"""
        steps = max(1, int(seconds * 30))
        for _ in range(steps):
            self.page.mouse.wheel(0, pixels / steps)
            self.page.wait_for_timeout(int(seconds * 1000 / steps))

    def scroll_to(self, locator, *, seconds: float = 1.0) -> None:
        """平滑滚动，让元素出现在屏幕上方。"""
        locator.evaluate("e => e.scrollIntoView({behavior: 'smooth', block: 'start'})")
        self.pause(seconds)
        self.scroll(-120, seconds=0.3)

    def button(self, pattern: str):
        return self.page.get_by_role("button", name=re.compile(LABELS.get(pattern, pattern))).first

    def visible(self, locator, timeout: float = 1.5) -> bool:
        try:
            locator.wait_for(state="visible", timeout=int(timeout * 1000))
            return True
        except Exception:  # noqa: BLE001 - Playwright 超时
            return False


# ---- 动作：每个返回前把画面停在要展示的地方 ------------------------------------


def script_lines(lang: str) -> list[str]:
    """演示脚本按句拆成段落，和界面「自己写口播脚本」的拆法一样。"""
    return [part.strip() for part in re.split(r"(?<=[。！？!?])\s*", DEMO_SCRIPT[lang]) if part.strip()]


def script_draft(lang: str) -> str:
    """打开首页时直接停在「口播脚本」这一步、段落都空着的草稿（不管有没有配大模型都一样）。"""
    import json

    count = len(script_lines(lang))
    draft = {
        "step": 3,
        "idea": "",
        "subject": "",
        "title": "",
        "lines": [""] * count,
        "videoType": "product_ad",
        "aspect": "16:9",
        "duration": 20,
        "language": "zh-CN" if lang == "zh" else "en-US",
    }
    return f"try {{ localStorage.setItem('shotwright.newVideoDraft', {json.dumps(json.dumps(draft))}); }} catch (e) {{}}"


def act_script(s: Session, demo: Demo) -> None:
    page = s.page
    boxes = page.locator(".script-line textarea")
    if not s.visible(boxes.first, 3):
        raise RuntimeError("首页没有停在「口播脚本」这一步")
    for index, line in enumerate(script_lines(demo.lang)):
        box = boxes.nth(index)
        s.click(box)
        box.fill(line)  # 「贴上」：一段一段出现
        s.pause(0.25)
    s.hover(page.locator(".flow-actions .btn.primary").first, 0.9)
    goto_editor(s, demo.base, demo.project, step=2)
    s.scroll(340, seconds=1.6)


def act_shots(s: Session, demo: Demo) -> None:
    page = s.page
    # 素材步骤：主体参考图（产品四种颜色、各个人物）
    look = page.locator(".look-subject, .subject-card, .look-section").first
    if s.visible(look):
        s.scroll_to(look, seconds=0.8)
        s.hover(look, 0.8)
    else:
        s.scroll(270, seconds=1.0)
    s.pause(0.8)
    # 画面步骤：每个镜头的画面来源
    s.click(page.locator(".editor-steps button").nth(1))
    s.pause(0.6)
    tabs = page.locator("article.shot").first.locator(".segmented button, [role=radio], [role=tab]")
    for index in range(min(4, tabs.count())):
        s.hover(tabs.nth(index), 0.45)
    s.scroll(470, seconds=1.8)
    s.pause(0.6)


def act_precheck(s: Session, demo: Demo) -> None:
    page = s.page
    risky = page.locator("article.shot", has=page.locator(".risks")).first
    if not s.visible(risky, 4):
        s.scroll(600, seconds=1.5)
        return
    s.scroll_to(risky, seconds=1.0)
    s.hover(risky.locator(".risks"), 1.2)
    fix = risky.get_by_role("button", name=re.compile(LABELS["ai_fix"]))
    if s.visible(fix, 0.5):
        s.hover(fix, 1.0)
    keyframes = risky.get_by_role("button", name=re.compile(LABELS["keyframes"]))
    if s.visible(keyframes, 0.5):
        s.hover(keyframes, 1.4)  # 会花钱：只指一下，不点


def act_rerender(s: Session, demo: Demo) -> None:
    page = s.page
    text = page.locator("article.shot textarea").first
    s.click(text)
    page.keyboard.press("Control+A")
    page.keyboard.insert_text(demo.new_line)
    s.pause(0.5)
    text.evaluate("e => e.blur()")  # 失焦保存
    s.pause(0.8)
    before = _render_count(demo)
    s.click(s.button("render"))
    gap_start = s.now() + 1.2
    started = time.monotonic()
    # 等这次渲染做完：渲染记录多了一条就是完成了（最多等 10 分钟）。8 秒内没有开始渲染就不等了。
    while time.monotonic() - started < 600:
        count, running = _render_state(demo)
        if count > before or (not running and time.monotonic() - started > 8):
            break
        page.wait_for_timeout(1000)
    page.wait_for_timeout(1500)  # 界面刷新出新成片
    took = time.monotonic() - started
    s.gaps.append((gap_start, s.now() - 0.6))
    s.note = f"重新渲染实测 {took:.0f} 秒" if demo.lang == "zh" else f"Re-render took {took:.0f}s"
    s.pause(1.6)


def act_versions(s: Session, demo: Demo) -> None:
    page = s.page
    s.click(page.locator(".editor-steps button").nth(4))
    s.pause(0.5)
    versions = page.locator(".versions").first
    if s.visible(versions):
        s.scroll_to(versions, seconds=1.0)
        s.hover(versions, 0.8)
        translate = s.button("translate")
        if s.visible(translate, 0.5):
            s.hover(translate, 0.8)
    goto_editor(s, demo.base, demo.other, step=2)
    s.scroll(400, seconds=1.6)


def act_settings(s: Session, demo: Demo) -> None:
    page = s.page
    cost = page.get_by_text(re.compile(r"预计费用|累计约|Estimated cost|spent")).first
    if s.visible(cost, 3):
        s.hover(cost, 1.2)
    s.click(page.get_by_role("link", name=re.compile(LABELS["settings"])).first)
    s.pause(0.8)
    s.scroll(600, seconds=2.4)


def idea_draft(lang: str) -> str:
    """打开首页时停在「说想法」这一步、想法还空着的草稿。"""
    import json

    draft = {
        "step": 1,
        "idea": "",
        "subject": "",
        "title": "",
        "lines": [],
        "videoType": "product_ad",
        "aspect": "9:16",
        "duration": 20,
        "language": "zh-CN" if lang == "zh" else "en-US",
    }
    return f"try {{ localStorage.setItem('shotwright.newVideoDraft', {json.dumps(json.dumps(draft))}); }} catch (e) {{}}"


def mock_brief(context, lang: str) -> None:
    """「说想法」那一段不调用大模型：拦下判断设置、整理诉求的请求，直接返回准备好的结果。

    界面、按钮、步骤都是真实的，只是 AI 的回答换成固定的一段，录出来每次都一样、不花钱。
    服务列表里也标成配好了文字模型，没配置的电脑上也能录到正常的新建流程。
    """
    import json

    def fulfill(route, body: dict, delay: float) -> None:
        time.sleep(delay)  # 留出「整理中…」的样子
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))

    context.route(
        "**/api/v2/brief/decide",
        lambda route: fulfill(
            route, {"video_type": "product_ad", "duration": 20, "aspect": "9:16", "reason": ""}, 0.4
        ),
    )
    context.route("**/api/v2/brief/normalize", lambda route: fulfill(route, {"brief": DEMO_BRIEF[lang]}, 1.4))

    def providers(route) -> None:
        response = route.fetch()
        data = response.json()
        llm = data.get("llm") or []
        if not any(item.get("default") and item.get("configured") for item in llm):
            data["llm"] = [
                {**item, "default": index == 0, "configured": True} for index, item in enumerate(llm)
            ] or [{"id": "openai", "name": "LLM", "default": True, "configured": True}]
        route.fulfill(response=response, body=json.dumps(data, ensure_ascii=False))

    context.route("**/api/v2/providers", providers)


def act_idea(s: Session, demo: Demo) -> None:
    page = s.page
    box = page.locator("#idea")
    if not s.visible(box, 4):
        raise RuntimeError("首页没有停在「说想法」这一步")
    s.click(box)
    page.keyboard.type(DEMO_IDEA[demo.lang], delay=28 if demo.lang == "zh" else 14)
    s.pause(0.5)
    s.click(page.locator(".flow-actions .btn.primary").first)
    subject = page.locator("#subject")
    if not s.visible(subject, 8):
        return
    s.pause(0.6)
    s.hover(subject, 0.8)
    # 诉求里【待补充】那一行：滚到它，光标放过去
    subject.evaluate("e => { e.scrollTop = e.scrollHeight * 0.35; }")
    s.pause(0.8)
    s.hover(page.locator(".flow-actions .btn.primary").first, 0.9)


def act_board(s: Session, demo: Demo) -> None:
    page = s.page
    s.click(s.button("board"))
    table = page.locator(".board-table, .board").first
    if not s.visible(table, 4):
        return
    s.pause(0.6)
    s.hover(page.locator(".board-thumb").first, 0.7)
    s.hover(page.locator(".board-table td.col-visual, .col-visual").nth(1), 0.8)
    s.hover(page.locator(".board-table td.col-source, .col-source").nth(1), 0.8)
    s.scroll(360, seconds=1.6)
    s.pause(0.4)
    csv = s.button("csv")
    if s.visible(csv, 0.5):
        s.hover(csv, 0.9)


def act_look(s: Session, demo: Demo) -> None:
    page = s.page
    ready = page.locator(".readiness").first
    if s.visible(ready, 3):
        s.scroll_to(ready, seconds=0.8)
        for index in range(min(3, page.locator(".readiness-list > *").count())):
            s.hover(page.locator(".readiness-list > *").nth(index), 0.45)
    subject = page.locator(".look-subject").first
    if s.visible(subject, 2):
        s.scroll_to(subject, seconds=1.0)
        images = subject.locator(".look-image-cell, .look-image")
        for index in range(min(4, images.count())):
            s.hover(images.nth(index), 0.45)
        s.scroll(320, seconds=1.4)
    s.pause(0.5)


def act_prompts(s: Session, demo: Demo) -> None:
    page = s.page
    s.click(s.button("prompts"))
    block = page.locator(".prompt-block").first
    if not s.visible(block, 5):
        return
    s.pause(0.5)
    s.hover(block.locator(".prompt-meta, .prompt-head").first, 0.8)
    s.hover(block.locator(".prompt-text").first, 1.0)
    edit = block.get_by_role("button", name=re.compile(LABELS["prompt_edit"]))
    if s.visible(edit, 0.8):
        s.click(edit)
        area = block.locator("textarea").first
        if s.visible(area, 2):
            s.click(area)
            page.keyboard.press("Control+End")
            s.pause(1.2)
            page.keyboard.press("Escape")  # 不保存：只演示可以整段改
            s.pause(0.5)
    s.scroll(420, seconds=1.4)


def act_cost(s: Session, demo: Demo) -> None:
    page = s.page
    side = page.locator("aside.side")
    delivery = side.locator(".delivery button, .delivery label")
    if delivery.count() > 2:
        s.hover(delivery.nth(1), 0.5)
        s.hover(delivery.nth(delivery.count() - 1), 0.5)
    cost = side.get_by_text(re.compile(r"累计约|AI generation so far|spent", re.I)).first
    if s.visible(cost, 1.5):
        s.hover(cost, 1.0)
    metrics = side.locator(".spend-metrics summary, .spend-metrics").first
    if s.visible(metrics, 1):
        s.click(metrics)
        s.pause(0.8)
    budget = side.locator(".budget input, .inline-field.budget").first
    if s.visible(budget, 1):
        s.hover(budget, 0.8)
    sample = side.get_by_role("button", name=re.compile(LABELS["sample"])).first
    if s.visible(sample, 1):
        s.hover(sample, 1.2)  # 只指一下：点了会跑一遍样片


def act_review(s: Session, demo: Demo) -> None:
    page = s.page
    s.click(s.button("review"))
    rows = page.locator(".review-row")
    if not s.visible(rows.first, 4):
        return
    s.pause(0.5)
    s.hover(rows.first.locator(".review-takes, .review-take").first, 0.8)
    approve = rows.first.get_by_role("button", name=re.compile(LABELS["approve"]))
    if s.visible(approve, 0.8):
        s.click(approve)  # 审片标记不花钱，只改演示副本
        s.pause(0.4)
    if rows.count() > 1:
        s.scroll_to(rows.nth(1), seconds=0.9)
        approve = rows.nth(1).get_by_role("button", name=re.compile(LABELS["approve"]))
        if s.visible(approve, 0.8):
            s.click(approve)
    note = page.locator(".review-note").first
    if s.visible(note, 0.6):
        s.hover(note, 0.8)
    s.scroll(300, seconds=1.2)


def act_audio(s: Session, demo: Demo) -> None:
    page = s.page
    steps = page.locator(".editor-steps button")
    # ③ 配音与字幕
    s.click(steps.nth(2))
    s.pause(0.5)
    selects = page.locator(".editor-main select")
    for index in range(min(2, selects.count())):
        s.hover(selects.nth(index), 0.5)
    s.scroll(420, seconds=1.4)
    s.pause(0.4)
    # ④ 音乐与音效
    s.click(steps.nth(3))
    s.pause(0.6)
    checks = page.locator(".editor-main input[type=checkbox]")
    for index in range(min(2, checks.count())):
        s.hover(checks.nth(index), 0.5)
    # ⑤ 标题与文字
    s.click(steps.nth(4))
    s.pause(0.6)
    s.scroll(300, seconds=1.2)


def act_cut(s: Session, demo: Demo) -> None:
    page = s.page
    words = page.locator(".cut-word")
    if not s.visible(words.first, 6):
        raise RuntimeError("剪辑页没有出现文稿：剪辑项目还没识别过原片？")
    count = words.count()
    # 点一个词：视频跳到那里
    s.click(words.nth(min(3, count - 1)))
    s.pause(0.6)
    # 拖选几个词，按 Delete 删掉
    start, stop = min(12, count - 1), min(17, count - 1)
    if stop > start and s.move_to(words.nth(start)):
        page.mouse.down()
        box = words.nth(stop).bounding_box()
        if box:
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=24)
        page.mouse.up()
        s.pause(0.5)
        page.keyboard.press("Delete")
        s.pause(0.9)
    fillers = s.button("fillers")
    if s.visible(fillers, 0.6) and fillers.is_enabled():
        s.click(fillers)
        s.pause(0.6)
    stats = page.locator(".cut-stats").first
    if s.visible(stats, 0.6):
        s.hover(stats, 0.8)
    undo = s.button("undo")
    if s.visible(undo, 0.6) and undo.is_enabled():
        s.click(undo)
        s.pause(0.5)
    find = s.button("find")
    if s.visible(find, 0.6):
        s.click(find)
        field = page.locator(".cut-find input").first
        if s.visible(field, 1):
            s.click(field)
            sample = words.nth(min(6, count - 1)).inner_text().strip(" ，。,.!?！？")[:6]
            page.keyboard.type(sample, delay=60)
            s.pause(0.9)


ACTIONS: dict[str, tuple[str, Callable[[Session, Demo], None]]] = {
    # 动作名 → （起始页面，动作）。起始页面里的 {project}/{copy} 换成项目 id，#step=N 是编辑器步骤。
    "idea": ("#/", act_idea),
    "script": ("#/", act_script),
    "board": ("#/p/{project}#step=2", act_board),
    "look": ("#/p/{project}#step=1", act_look),
    "shots": ("#/p/{project}#step=1", act_shots),
    "precheck": ("#/p/{project}#step=2", act_precheck),
    "rerender": ("#/p/{copy}#step=2", act_rerender),
    "versions": ("#/p/{project}#step=5", act_versions),
    "settings": ("#/p/{project}#step=2", act_settings),
    "prompts": ("#/p/{project}#step=2", act_prompts),
    "cost": ("#/p/{project}#step=2", act_cost),
    "review": ("#/p/{copy}#step=2", act_review),
    "audio": ("#/p/{project}#step=3", act_audio),
    "cut": ("#/cut/{cut}/{cut_asset}", act_cut),
}


def _render_state(demo: Demo) -> tuple[int, bool]:
    """（已完成的渲染数，是否有渲染在进行）。"""
    import json
    import urllib.request

    with urllib.request.urlopen(f"{demo.base}/api/v2/projects/{demo.edit_copy}/renders", timeout=10) as resp:
        data = json.load(resp)
    running = any(job["state"] in ("queued", "running") for job in data["jobs"])
    return len(data["renders"]), running


def _render_count(demo: Demo) -> int:
    return _render_state(demo)[0]


def goto_editor(s: Session, base: str, project_id: str, *, step: int) -> None:
    s.page.evaluate(
        "([id, step]) => localStorage.setItem('shotwright.editorStep.' + id, String(step))",
        [project_id, step],
    )
    s.page.goto(f"{base}/#/p/{project_id}")
    s.page.locator("article.shot, .editor-steps").first.wait_for(timeout=30_000)
    s.pause(0.8)


class Recorder:
    def __init__(self, demo: Demo, work: Path, *, headless: bool = True):
        from playwright.sync_api import sync_playwright

        self.demo = demo
        self.work = work
        self.work.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=headless)

    def close(self) -> None:
        self.browser.close()
        self._pw.stop()

    def record_scene(self, name: str, seconds: float, font: Path, *, card: str = "", tag: str = "") -> Clip:
        """录一段网页动画（scenes.py），时长按配音来。``card`` 是章节卡上的字。"""
        from scenes import SCENES

        html = SCENES[name](self.demo.lang, seconds, font, card=card)
        name = f"{name}{tag}"
        page_file = self.work / f"scene-{name}.html"
        page_file.write_text(html, encoding="utf-8")
        context = self.browser.new_context(
            viewport=VIEWPORT, record_video_dir=str(self.work / f"raw-{name}"), record_video_size=VIEWPORT
        )
        page = context.new_page()
        started = time.monotonic()
        page.goto(page_file.resolve().as_uri())
        page.evaluate("document.fonts.ready")
        ready = time.monotonic() - started
        page.wait_for_timeout(int((seconds + 1.0) * 1000))
        end = time.monotonic() - started
        video = page.video
        context.close()
        return Clip(Path(video.path()), ready, end)

    def record(self, name: str, seconds: float) -> Clip:
        """录一段动作，录像至少比配音长一点；返回原始录像和要保留的范围。"""
        start_page, action = ACTIONS[name]
        demo = self.demo
        if name == "cut" and not demo.cut_project:
            raise RuntimeError("没有识别过原片的剪辑项目：用 --cut-demo 指定一个")
        target = start_page.format(
            project=demo.project, copy=demo.edit_copy, cut=demo.cut_project, cut_asset=demo.cut_asset
        )
        step = None
        if "#step=" in target:
            target, step_text = target.split("#step=")
            step = int(step_text)
        raw_dir = self.work / f"raw-{name}"
        context = self.browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(raw_dir),
            record_video_size=VIEWPORT,
            locale="zh-CN" if demo.lang == "zh" else "en-US",
        )
        context.add_init_script(CURSOR_JS)
        if name == "script":
            context.add_init_script(script_draft(demo.lang))
        if name == "idea":
            context.add_init_script(idea_draft(demo.lang))
            mock_brief(context, demo.lang)
        context.add_init_script(
            f"try {{ localStorage.setItem('shotwright.ui-lang', '{demo.lang}'); }} catch (e) {{}}"
        )
        # 关掉简洁模式：宣传片要拍的是完整的镜头卡片（画面来源、生成前检查），
        # 简洁模式默认开着，会把这几块收起来。
        context.add_init_script("try { localStorage.setItem('shotwright.simple', '0'); } catch (e) {}")
        if step is not None:
            project_id = target.rsplit("/", 1)[-1]
            context.add_init_script(
                f"try {{ localStorage.setItem('shotwright.editorStep.{project_id}', '{step}'); }} catch (e) {{}}"
            )
        page = context.new_page()
        started = time.monotonic()
        page.goto(f"{demo.base}/{target}")
        page.locator("main, #root > *").first.wait_for(timeout=30_000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        session = Session(page, started)
        page.mouse.move(session.x, session.y)
        ready = session.now()
        action(session, demo)
        kept = session.now() - ready - sum(b - a for a, b in session.gaps)
        if kept < seconds + 0.8:
            session.pause(seconds + 0.8 - kept)
        end = session.now()
        video = page.video
        context.close()
        path = Path(video.path()) if video else None
        if path is None or not path.is_file():
            raise RuntimeError(f"{name}：没有录到视频")
        return Clip(path, ready, end, session.gaps, session.note)


def finish(
    clip: Clip, seconds: float, out: Path, *, ffmpeg: str = "ffmpeg", font: Path | None = None
) -> Path:
    """按保留范围裁剪、剪掉长等待，比配音长太多就加速；输出 H.264 MP4。"""
    pieces: list[tuple[float, float]] = []
    cursor = clip.ready
    for start, stop in sorted(clip.gaps):
        if start > cursor:
            pieces.append((cursor, start))
        cursor = max(cursor, stop)
    pieces.append((cursor, clip.end))
    length = sum(b - a for a, b in pieces)
    speed = min(MAX_SPEEDUP, max(1.0, length / (seconds + 0.5)))
    parts = []
    for index, (a, b) in enumerate(pieces):
        parts.append(f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[p{index}]")
    joined = "".join(f"[p{i}]" for i in range(len(pieces)))
    chain = (
        ";".join(parts)
        + f";{joined}concat=n={len(pieces)}:v=1:a=0[c];[c]setpts=PTS/{speed:.3f},fps=30,"
        + "scale=1920:1080:flags=lanczos,unsharp=5:5:0.6"
    )
    if clip.note and clip.gaps and font is not None:
        # 剪掉等待的那一刻起显示 2 秒：告诉观众这里省略了多久。
        first_gap = sorted(clip.gaps)[0][0] - clip.ready
        at = first_gap / speed
        text = clip.note.replace(":", r"\:")
        chain += (
            f",drawtext=fontfile='{font.as_posix()}':text='{text}':x=w-tw-60:y=60:fontsize=44:fontcolor=white:"
            f"box=1:boxcolor=black@0.55:boxborderw=18:enable='between(t,{at:.2f},{at + 2.5:.2f})'"
        )
    chain += "[v]"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(clip.path),
            "-filter_complex",
            chain,
            "-map",
            "[v]",
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
