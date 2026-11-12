"""不录界面的镜头（开场介绍、痛点、章节卡、片尾）：用网页动画做成动态画面，再用浏览器录下来。

每个场景是一段 HTML + CSS 动画，按配音时长排好出场节奏；文字用仓库自带的思源黑体，
图标是内嵌的 SVG，不依赖电脑上装了什么字体。画面底部留出字幕的位置。
"""

from __future__ import annotations

from pathlib import Path

ICONS = {
    # 工具来回切：两个方向相反的箭头
    "switch": '<svg viewBox="0 0 24 24"><path d="M4 7h13l-3-3M20 17H7l3 3" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    # 生成翻车：骰子
    "dice": '<svg viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="17" height="17" rx="4" fill="none" '
    'stroke="currentColor" stroke-width="2"/><circle cx="8.5" cy="8.5" r="1.6" fill="currentColor"/>'
    '<circle cx="15.5" cy="15.5" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/></svg>',
    # 费用不明：钱袋加问号
    "cost": '<svg viewBox="0 0 24 24"><path d="M9 4h6l-1.5 3h-3zM6.5 20h11a2 2 0 0 0 2-2.3C18.8 12 16 8 12 8s-6.8 4-7.5 9.7'
    'A2 2 0 0 0 6.5 20z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>'
    '<path d="M10.5 12.5a1.6 1.6 0 1 1 2.2 1.5c-.5.2-.7.6-.7 1.1M12 17.3v.1" fill="none" stroke="currentColor" '
    'stroke-width="1.8" stroke-linecap="round"/></svg>',
}

PAIN = {
    "zh": {
        "kicker": "做 AI 广告的日常",
        "cards": [
            ("switch", "工具来回切", "写脚本、配音、生成、剪辑，四五个工具来回倒"),
            ("dice", "生成翻车", "手指变形、文字乱码，只能一遍遍重抽"),
            ("cost", "费用不明", "钱花出去了，却说不清花在哪"),
        ],
    },
    "en": {
        "kicker": "Making AI ads today",
        "cards": [
            ("switch", "Juggling tools", "Script, voice, generation, editing: four or five apps"),
            ("dice", "Failed shots", "Warped fingers, garbled text, roll again and again"),
            ("cost", "Unknown costs", "Money goes out, nobody knows where"),
        ],
    },
}

OUTRO = {
    "zh": {
        "name": "Shotwright",
        "cn": "镜匠",
        "tagline": "一份脚本，变成一组能投放的广告",
        "badges": ["开源免费", "本地运行", "模型任选"],
        "url": "github.com/rushansishui/Shotwright",
    },
    "en": {
        "name": "Shotwright",
        "cn": "",
        "tagline": "One script, a set of ads ready to run",
        "badges": ["Open source", "Runs locally", "Any model"],
        "url": "github.com/rushansishui/Shotwright",
    },
}

BASE_CSS = """
@font-face { font-family: Promo; src: url('{font}'); font-weight: 400 900; }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 1280px; height: 720px; overflow: hidden; }
body { font-family: Promo, 'Noto Sans SC', 'Microsoft YaHei', 'PingFang SC', sans-serif; color: #F8FAFC;
  background: radial-gradient(1200px 700px at 20% 10%, #312E81 0%, transparent 60%),
              radial-gradient(900px 600px at 90% 90%, #7F1D1D 0%, transparent 55%), #0B1020; }
.stage { position: absolute; inset: 0 0 150px 0; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 34px; }
@keyframes rise { from { opacity: 0; transform: translateY(28px) scale(.96); } to { opacity: 1; transform: none; } }
@keyframes fade { from { opacity: 0; } to { opacity: 1; } }
@keyframes drift { from { transform: scale(1); } to { transform: scale(1.04); } }
.bg { position: absolute; inset: -40px; animation: drift {total}s ease-in-out forwards; }
"""


def _page(css: str, body: str, *, total: float, font: Path) -> str:
    base = BASE_CSS.replace("{font}", font.resolve().as_uri()).replace("{total}", f"{total:.2f}")
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{base}{css}</style></head><body>{body}</body></html>"


def pain_html(lang: str, seconds: float, font: Path, **_: str) -> str:
    data = PAIN[lang]
    step = max(0.45, min(1.1, (seconds - 1.2) / 3))
    cards = "".join(
        f"""<div class="card" style="animation-delay:{0.35 + i * step:.2f}s">
              <div class="icon">{ICONS[icon]}</div><h2>{title}</h2><p>{text}</p></div>"""
        for i, (icon, title, text) in enumerate(data["cards"])
    )
    css = """
.kicker { font-size: 32px; letter-spacing: .12em; color: #FCA5A5; opacity: 0; animation: fade .5s ease-out .05s forwards; }
.cards { display: flex; gap: 24px; }
.card { width: 368px; padding: 36px 32px 38px; border-radius: 22px; background: rgba(15, 23, 42, .72);
  border: 1px solid rgba(248, 113, 113, .35); box-shadow: 0 18px 50px rgba(0,0,0,.35);
  opacity: 0; animation: rise .6s cubic-bezier(.2,.8,.2,1) forwards; }
.icon { width: 80px; height: 80px; border-radius: 18px; display: grid; place-items: center; color: #FCA5A5;
  background: rgba(239, 68, 68, .16); margin-bottom: 22px; }
.icon svg { width: 46px; height: 46px; }
h2 { font-size: 46px; font-weight: 800; margin-bottom: 12px; }
p { font-size: 24px; line-height: 1.5; color: #CBD5E1; }
"""
    body = f"""<div class="bg"></div><div class="stage"><div class="kicker">{data["kicker"]}</div>
<div class="cards">{cards}</div></div>"""
    return _page(css, body, total=seconds + 1, font=font)


def outro_html(lang: str, seconds: float, font: Path, **_: str) -> str:
    data = OUTRO[lang]
    badges = "".join(
        f'<span style="animation-delay:{0.9 + i * 0.18:.2f}s">{badge}</span>'
        for i, badge in enumerate(data["badges"])
    )
    cn = f'<span class="cn">{data["cn"]}</span>' if data["cn"] else ""
    css = """
.logo { width: 120px; height: 120px; border-radius: 30px; background: #DC2626; display: grid; place-items: center;
  box-shadow: 0 20px 60px rgba(220, 38, 38, .45); opacity: 0; animation: rise .6s cubic-bezier(.2,.8,.2,1) .1s forwards; }
.logo i { width: 62px; height: 62px; border: 9px solid #fff; border-radius: 14px; display: block; }
.name { font-size: 76px; font-weight: 800; letter-spacing: .01em; opacity: 0; animation: rise .6s ease-out .35s forwards; }
.name .cn { font-size: 48px; font-weight: 600; color: #CBD5E1; margin-left: 18px; }
.tagline { font-size: 32px; color: #E2E8F0; opacity: 0; animation: fade .6s ease-out .6s forwards; }
.badges { display: flex; gap: 16px; }
.badges span { padding: 10px 22px; border-radius: 999px; font-size: 24px; background: rgba(255,255,255,.08);
  border: 1px solid rgba(255,255,255,.22); opacity: 0; animation: rise .5s ease-out forwards; }
.url { font-size: 28px; color: #FDE68A; letter-spacing: .02em; opacity: 0; animation: fade .6s ease-out 1.5s forwards; }
"""
    body = f"""<div class="bg"></div><div class="stage" style="gap:26px">
<div class="logo"><i></i></div><div class="name">{data["name"]}{cn}</div>
<div class="tagline">{data["tagline"]}</div><div class="badges">{badges}</div><div class="url">{data["url"]}</div></div>"""
    return _page(css, body, total=seconds + 1, font=font)


INTRO = {
    "zh": {
        "tagline": "开源 · 本地运行的 AI 短视频工具",
        "script": "脚本",
        "film": "成片",
        "shots": ["素材库", "AI 视频", "AI 图片", "自己拍的"],
        "shot": "镜头",
        "auto": ["配音", "字幕", "音乐"],
    },
    "en": {
        "tagline": "Open source AI video, running on your machine",
        "script": "Script",
        "film": "Video",
        "shots": ["Stock", "AI video", "AI image", "Your footage"],
        "shot": "Shot",
        "auto": ["Voice", "Subtitles", "Music"],
    },
}

SHOT_COLORS = ["#0EA5E9", "#DC2626", "#A855F7", "#16A34A"]

DOC = (
    '<svg viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6z" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linejoin="round"/><path d="M9 11h6M9 14.5h6M9 18h4" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round"/></svg>'
)
PLAY = (
    '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="3" fill="none" stroke="currentColor" '
    'stroke-width="1.8"/><path d="M10 9.2v5.6l4.6-2.8z" fill="currentColor"/></svg>'
)


def intro_html(lang: str, seconds: float, font: Path, **_: str) -> str:
    """开场：名字，然后「脚本 → 一个个镜头（各选各的画面）→ 成片」，配音、字幕、音乐自动接上。"""
    data = INTRO[lang]
    beat = max(0.35, min(0.9, (seconds - 2.0) / 9))
    t0 = 1.0
    tiles = "".join(
        f"""<div class="tile" style="animation-delay:{t0 + beat * (2 + i * 0.6):.2f}s;--c:{SHOT_COLORS[i]}">
              <b>{data["shot"]} {i + 1}</b><span>{name}</span></div>"""
        for i, name in enumerate(data["shots"])
    )
    autos = "".join(
        f'<span style="animation-delay:{t0 + beat * (6.4 + i * 0.4):.2f}s">{name}</span>'
        for i, name in enumerate(data["auto"])
    )
    css = """
.head { display: flex; align-items: center; gap: 22px; opacity: 0; animation: rise .6s ease-out .1s forwards; }
.logo { width: 76px; height: 76px; border-radius: 20px; background: #DC2626; display: grid; place-items: center;
  box-shadow: 0 14px 40px rgba(220, 38, 38, .45); }
.logo i { width: 40px; height: 40px; border: 6px solid #fff; border-radius: 10px; display: block; }
.name { font-size: 58px; font-weight: 800; }
.name small { font-size: 36px; color: #CBD5E1; margin-left: 12px; font-weight: 600; }
.tagline { font-size: 26px; color: #E2E8F0; opacity: 0; animation: fade .6s ease-out .5s forwards; }
.flow { display: flex; align-items: center; gap: 18px; margin-top: 10px; }
.node { width: 150px; height: 150px; border-radius: 22px; background: rgba(15,23,42,.75);
  border: 1px solid rgba(255,255,255,.18); display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 10px; font-size: 26px; font-weight: 700; opacity: 0;
  animation: rise .5s cubic-bezier(.2,.8,.2,1) forwards; }
.node svg { width: 56px; height: 56px; color: #FDE68A; }
.arrow { font-size: 40px; color: #94A3B8; opacity: 0; animation: fade .4s ease-out forwards; }
.tiles { display: grid; grid-template-columns: repeat(4, 118px); gap: 12px; }
.tile { height: 150px; border-radius: 16px; background: rgba(15,23,42,.8); border: 2px solid var(--c);
  display: flex; flex-direction: column; justify-content: space-between; padding: 14px 12px; opacity: 0;
  animation: rise .45s cubic-bezier(.2,.8,.2,1) forwards; box-shadow: 0 10px 30px rgba(0,0,0,.3); }
.tile b { font-size: 18px; color: #CBD5E1; font-weight: 600; }
.tile span { font-size: 21px; font-weight: 800; color: var(--c); line-height: 1.2; }
.autos { display: flex; gap: 14px; }
.autos span { padding: 8px 20px; border-radius: 999px; font-size: 22px; background: rgba(255,255,255,.08);
  border: 1px solid rgba(255,255,255,.22); opacity: 0; animation: rise .45s ease-out forwards; }
"""
    cn = "<small>镜匠</small>" if lang == "zh" else ""
    body = f"""<div class="bg"></div><div class="stage" style="gap:26px">
<div class="head"><div class="logo"><i></i></div><div class="name">Shotwright{cn}</div></div>
<div class="tagline">{data["tagline"]}</div>
<div class="flow">
  <div class="node" style="animation-delay:{t0 + beat * 0.6:.2f}s">{DOC}{data["script"]}</div>
  <div class="arrow" style="animation-delay:{t0 + beat * 1.4:.2f}s">→</div>
  <div class="tiles">{tiles}</div>
  <div class="arrow" style="animation-delay:{t0 + beat * 5.0:.2f}s">→</div>
  <div class="node" style="animation-delay:{t0 + beat * 5.6:.2f}s">{PLAY}{data["film"]}</div>
</div>
<div class="autos">{autos}</div></div>"""
    return _page(css, body, total=seconds + 1, font=font)


def chapter_html(lang: str, seconds: float, font: Path, card: str = "", **_: str) -> str:
    """章节卡：大号编号和章节名，一条红线从左往右画出来。``card`` 是「01|章节名」。"""
    number, _sep, title = card.partition("|")
    kicker = f"第 {int(number)} 部分" if lang == "zh" and number.isdigit() else f"Part {int(number)}"
    if number.isdigit() and int(number) == 7 and lang == "zh":
        kicker = "最后"
    css = """
.stage { align-items: flex-start; padding-left: 170px; gap: 18px; }
.num { font-size: 150px; font-weight: 900; line-height: 1; color: #DC2626; letter-spacing: -.02em;
  opacity: 0; animation: rise .5s cubic-bezier(.2,.8,.2,1) .05s forwards; }
.kicker { font-size: 28px; letter-spacing: .14em; color: #FCA5A5; opacity: 0; animation: fade .5s ease-out .25s forwards; }
.title { font-size: 72px; font-weight: 800; opacity: 0; animation: rise .55s ease-out .35s forwards; }
.line { height: 6px; width: 0; border-radius: 3px; background: linear-gradient(90deg, #DC2626, #F59E0B);
  animation: grow .9s cubic-bezier(.2,.8,.2,1) .5s forwards; }
@keyframes grow { to { width: 520px; } }
"""
    body = f"""<div class="bg"></div><div class="stage">
<div class="num">{number}</div><div class="kicker">{kicker}</div><div class="title">{title}</div>
<div class="line"></div></div>"""
    return _page(css, body, total=seconds + 1, font=font)


SCENES = {"intro": intro_html, "pain": pain_html, "chapter": chapter_html, "outro": outro_html}
