"""示例项目：第一次打开时一键建好一个完整的示例，不用任何 Key、不花钱就能直接渲染。

示例是一款虚构的冷萃咖啡「晨屿」的 5 个镜头带货短视频：文案、镜头小标题、常驻大标题、
价格和购买引导叠加文字都填好了，画面是程序现画的插画（扁平风格，不含文字），渲染时自动
加上缓慢推近。用户点开就能看到「做好的视频长什么样」，直接点渲染出成片，再照着改成自己的。

插画用 Pillow 现画，不打包图片文件，也不会有版权问题。
"""

from __future__ import annotations

import math
import random
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from shotwright.core.models import (
    Canvas,
    Look,
    Overlay,
    Project,
    Shot,
    ShotVisual,
    VoiceSettings,
)
from shotwright.core.store import ProjectStore
from shotwright.services import localize

SCALE = 2
"""先按 2 倍尺寸画再缩小，边缘更平滑。"""

CREAM = (255, 244, 228)
COFFEE = (74, 44, 30)
CARAMEL = (196, 128, 72)
MILK = (250, 236, 214)
SKY_TOP = (255, 186, 120)
SKY_BOTTOM = (255, 232, 196)
SEA = (64, 120, 160)
TEAL = (38, 84, 110)
NIGHT = (36, 40, 66)


@dataclass(frozen=True)
class SampleShot:
    text: str
    title: str
    scene: str
    """插画：sunrise / slow / glass / commute / offer。"""
    camera: str = "push_in"


SHOTS: dict[str, list[SampleShot]] = {
    "zh": [
        SampleShot("早上起不来？一瓶冷萃，三秒清醒。", "早八救星", "sunrise"),
        SampleShot("低温慢萃十二小时，口感顺滑不发苦。", "慢萃 12 小时", "slow"),
        SampleShot("加冰加奶都好喝，在家也能做拿铁。", "加冰加奶", "glass"),
        SampleShot("小瓶装随手一带，通勤路上刚刚好。", "随身小瓶", "commute"),
        SampleShot("现在下单，第二瓶半价，点下方链接。", "限时福利", "offer"),
    ],
    "en": [
        SampleShot("Can't wake up? One cold brew, and you're up.", "Morning fix", "sunrise"),
        SampleShot("Steeped cold for twelve hours. Smooth, never bitter.", "12-hour brew", "slow"),
        SampleShot("Add ice, add milk. A latte at home in seconds.", "Ice & milk", "glass"),
        SampleShot("A small bottle that fits your bag and your commute.", "Grab & go", "commute"),
        SampleShot("Order now and get the second bottle half off.", "Limited offer", "offer"),
    ],
}

TEXTS = {
    "zh": {
        "title": "示例：晨屿冷萃咖啡",
        "subject": "虚构品牌「晨屿」冷萃咖啡的带货短视频，面向早上起不来的上班族，突出低温慢萃、口感顺滑、方便携带，结尾引导下单。",
        "headline": "晨屿冷萃",
        "price": "第二瓶半价",
        "cta": "点下方链接下单",
        "subjects": "冷萃咖啡：深棕色玻璃小瓶，奶油色标签",
        "style": "扁平插画风格，暖色调，晨光",
    },
    "en": {
        "title": "Sample: Isle Cold Brew",
        "subject": 'Short product ad for a fictional cold brew brand "Isle", for office workers who struggle in the morning: slow cold brew, smooth taste, easy to carry, ends with a call to order.',
        "headline": "Isle Cold Brew",
        "price": "2nd bottle 50% off",
        "cta": "Tap the link below",
        "subjects": "Cold brew: small dark-brown glass bottle with a cream label",
        "style": "Flat illustration, warm tones, morning light",
    },
}


def _texts(language: str) -> tuple[dict[str, str], list[SampleShot]]:
    key = "zh" if language.lower().startswith("zh") else "en"
    return TEXTS[key], SHOTS[key]


# ---- 插画 -------------------------------------------------------------------


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    width, height = size
    column = Image.new("RGB", (1, height))
    for y in range(height):
        k = y / max(1, height - 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * k) for a, b in zip(top, bottom, strict=True)))
    return column.resize((width, height))


def _bottle(draw: ImageDraw.ImageDraw, cx: float, bottom: float, h: float, *, label=CREAM) -> None:
    """一瓶冷萃：深棕玻璃瓶、瓶盖、奶油色标签（不写字，用色块代替）。"""
    w = h * 0.36
    neck_w, neck_h = w * 0.46, h * 0.16
    body_top = bottom - h * 0.74
    shoulder = h * 0.1
    draw.rounded_rectangle((cx - w / 2, body_top, cx + w / 2, bottom), radius=w * 0.22, fill=COFFEE)
    draw.polygon(
        [
            (cx - w / 2 + w * 0.04, body_top + w * 0.2),
            (cx - neck_w / 2, body_top - shoulder),
            (cx + neck_w / 2, body_top - shoulder),
            (cx + w / 2 - w * 0.04, body_top + w * 0.2),
        ],
        fill=COFFEE,
    )
    neck_top = body_top - shoulder - neck_h
    draw.rectangle((cx - neck_w / 2, neck_top, cx + neck_w / 2, body_top - shoulder + 2), fill=COFFEE)
    draw.rounded_rectangle(
        (cx - neck_w / 2 - w * 0.04, neck_top - h * 0.07, cx + neck_w / 2 + w * 0.04, neck_top + h * 0.01),
        radius=w * 0.05,
        fill=CARAMEL,
    )
    top, bot = body_top + h * 0.2, body_top + h * 0.52
    draw.rounded_rectangle((cx - w / 2, top, cx + w / 2, bot), radius=w * 0.04, fill=label)
    draw.ellipse(
        (cx - w * 0.16, top + (bot - top) * 0.18, cx + w * 0.16, top + (bot - top) * 0.18 + w * 0.32),
        fill=CARAMEL,
    )
    for i, ratio in enumerate((0.62, 0.74)):
        y = top + (bot - top) * ratio
        half = w * (0.3 if i == 0 else 0.2)
        draw.rounded_rectangle((cx - half, y, cx + half, y + h * 0.018), radius=h * 0.009, fill=COFFEE)
    # 玻璃高光
    draw.rounded_rectangle(
        (cx - w * 0.36, body_top + h * 0.56, cx - w * 0.26, bottom - h * 0.06),
        radius=w * 0.05,
        fill=(120, 80, 58),
    )


def _bean(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, angle: float) -> None:
    """一颗咖啡豆：椭圆加中间一道弯缝。"""
    pts = []
    for i in range(48):
        t = 2 * math.pi * i / 48
        px, py = r * 0.72 * math.cos(t), r * math.sin(t)
        pts.append(
            (x + px * math.cos(angle) - py * math.sin(angle), y + px * math.sin(angle) + py * math.cos(angle))
        )
    draw.polygon(pts, fill=(96, 58, 38))
    seam = []
    for i in range(13):
        k = -0.8 + 1.6 * i / 12
        px, py = r * 0.12 * math.sin(k * math.pi), r * k
        seam.append(
            (x + px * math.cos(angle) - py * math.sin(angle), y + px * math.sin(angle) + py * math.cos(angle))
        )
    draw.line(seam, fill=(58, 34, 22), width=max(2, int(r * 0.12)))


def _sunrise(img: Image.Image) -> None:
    w, h = img.size
    img.paste(_gradient((w, h), SKY_TOP, SKY_BOTTOM))
    draw = ImageDraw.Draw(img)
    sun_y = h * 0.5
    draw.ellipse(
        (w * 0.5 - w * 0.26, sun_y - w * 0.26, w * 0.5 + w * 0.26, sun_y + w * 0.26), fill=(255, 214, 140)
    )
    draw.ellipse(
        (w * 0.5 - w * 0.18, sun_y - w * 0.18, w * 0.5 + w * 0.18, sun_y + w * 0.18), fill=(255, 238, 190)
    )
    draw.rectangle((0, h * 0.56, w, h), fill=SEA)
    for i in range(9):
        y = h * 0.59 + i * h * 0.035
        half = w * (0.08 + 0.03 * i)
        draw.rounded_rectangle(
            (w / 2 - half, y, w / 2 + half, y + h * 0.006), radius=h * 0.003, fill=(255, 214, 160)
        )
    # 小岛
    draw.ellipse((-w * 0.2, h * 0.6, w * 0.55, h * 0.78), fill=TEAL)
    draw.ellipse((w * 0.6, h * 0.62, w * 1.3, h * 0.82), fill=TEAL)
    draw.rectangle((0, h * 0.74, w, h), fill=(232, 196, 150))
    _bottle(draw, w * 0.5, h * 0.9, h * 0.36)


def _slow(img: Image.Image) -> None:
    w, h = img.size
    img.paste(_gradient((w, h), (60, 44, 40), (30, 22, 20)))
    draw = ImageDraw.Draw(img)
    # 冷萃滴滤：上面一只玻璃壶，水滴落进下面的瓶子
    cx = w * 0.5
    draw.rounded_rectangle(
        (cx - w * 0.22, h * 0.12, cx + w * 0.22, h * 0.32), radius=w * 0.06, fill=(150, 196, 214)
    )
    draw.rounded_rectangle(
        (cx - w * 0.2, h * 0.2, cx + w * 0.2, h * 0.31), radius=w * 0.05, fill=(110, 160, 186)
    )
    draw.polygon([(cx - w * 0.12, h * 0.32), (cx + w * 0.12, h * 0.32), (cx, h * 0.38)], fill=(150, 196, 214))
    for i, y in enumerate((0.41, 0.46, 0.51)):
        r = w * (0.018 - i * 0.003)
        draw.ellipse((cx - r, h * y - r * 1.4, cx + r, h * y + r), fill=(200, 140, 90))
    _bottle(draw, cx, h * 0.86, h * 0.3)
    # 钟面：慢慢走的 12 小时
    ox, oy, r = w * 0.8, h * 0.6, w * 0.13
    draw.ellipse((ox - r, oy - r, ox + r, oy + r), fill=CREAM)
    for i in range(12):
        a = 2 * math.pi * i / 12
        draw.ellipse(
            (
                ox + r * 0.8 * math.sin(a) - r * 0.05,
                oy - r * 0.8 * math.cos(a) - r * 0.05,
                ox + r * 0.8 * math.sin(a) + r * 0.05,
                oy - r * 0.8 * math.cos(a) + r * 0.05,
            ),
            fill=CARAMEL,
        )
    draw.line((ox, oy, ox, oy - r * 0.6), fill=COFFEE, width=int(r * 0.08))
    draw.line((ox, oy, ox + r * 0.45, oy + r * 0.1), fill=COFFEE, width=int(r * 0.08))
    rng = random.Random(7)
    for _ in range(14):
        _bean(
            draw,
            rng.uniform(0, w),
            rng.uniform(h * 0.88, h),
            w * rng.uniform(0.03, 0.045),
            rng.uniform(0, 3.1),
        )


def _glass(img: Image.Image) -> None:
    w, h = img.size
    img.paste(_gradient((w, h), (240, 214, 186), (214, 170, 130)))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, h * 0.72, w, h), fill=(170, 116, 78))
    cx, top, bottom = w * 0.46, h * 0.32, h * 0.8
    half_top, half_bottom = w * 0.25, w * 0.2
    glass = [
        (cx - half_top, top),
        (cx + half_top, top),
        (cx + half_bottom, bottom),
        (cx - half_bottom, bottom),
    ]
    draw.polygon(glass, fill=(236, 240, 242))

    # 咖啡和奶分层
    def at(y: float) -> float:
        return half_top + (half_bottom - half_top) * (y - top) / (bottom - top)

    for y0, y1, color in ((0.42, 0.58, MILK), (0.58, 0.79, (122, 76, 48))):
        a, b = h * y0, h * y1
        draw.polygon(
            [(cx - at(a) + 8, a), (cx + at(a) - 8, a), (cx + at(b) - 8, b), (cx - at(b) + 8, b)], fill=color
        )
    swirl = [
        (cx - at(h * 0.58) + 8 + i * 6, h * 0.58 + 22 * math.sin(i / 9))
        for i in range(int(2 * at(h * 0.58) / 6) - 2)
    ]
    draw.line(swirl, fill=(196, 150, 112), width=18)
    rng = random.Random(3)
    for _ in range(5):
        x, y, s = (
            cx + rng.uniform(-w * 0.12, w * 0.1),
            h * rng.uniform(0.37, 0.5),
            w * rng.uniform(0.07, 0.09),
        )
        draw.rounded_rectangle(
            (x - s / 2, y - s / 2, x + s / 2, y + s / 2),
            radius=s * 0.2,
            fill=(210, 232, 240),
            outline=(255, 255, 255),
            width=6,
        )
    draw.rounded_rectangle((cx + w * 0.06, h * 0.18, cx + w * 0.09, h * 0.6), radius=12, fill=(222, 92, 72))
    _bottle(draw, w * 0.8, h * 0.82, h * 0.26)


def _commute(img: Image.Image) -> None:
    w, h = img.size
    img.paste(_gradient((w, h), (150, 196, 224), (226, 238, 244)))
    draw = ImageDraw.Draw(img)
    rng = random.Random(11)
    x = -w * 0.05
    while x < w:
        bw, bh = w * rng.uniform(0.12, 0.2), h * rng.uniform(0.18, 0.4)
        shade = rng.choice([(96, 128, 160), (120, 150, 180), (80, 110, 140)])
        draw.rectangle((x, h * 0.68 - bh, x + bw, h * 0.68), fill=shade)
        for wy in range(int(h * 0.68 - bh + 30), int(h * 0.66), 70):
            for wx in range(int(x + 24), int(x + bw - 30), 60):
                draw.rectangle((wx, wy, wx + 26, wy + 36), fill=(214, 228, 240))
        x += bw + w * 0.01
    draw.rectangle((0, h * 0.68, w, h), fill=(84, 92, 108))
    for i in range(6):
        draw.rectangle((i * w * 0.2, h * 0.8, i * w * 0.2 + w * 0.1, h * 0.81), fill=(230, 230, 230))
    # 通勤包，侧袋里插着一瓶冷萃
    cx, top = w * 0.48, h * 0.5
    _bottle(draw, cx + w * 0.22, h * 0.7, h * 0.24)
    draw.rounded_rectangle((cx - w * 0.3, top, cx + w * 0.26, h * 0.86), radius=w * 0.06, fill=(214, 120, 84))
    draw.arc(
        (cx - w * 0.2, top - h * 0.12, cx + w * 0.16, top + h * 0.08), 180, 360, fill=(160, 84, 58), width=28
    )
    draw.rounded_rectangle(
        (cx - w * 0.22, top + h * 0.08, cx + w * 0.18, top + h * 0.16), radius=20, fill=(190, 100, 70)
    )
    draw.rounded_rectangle(
        (cx + w * 0.12, h * 0.66, cx + w * 0.34, h * 0.84), radius=w * 0.03, fill=(190, 100, 70)
    )


def _offer(img: Image.Image) -> None:
    w, h = img.size
    img.paste(_gradient((w, h), (255, 222, 170), (246, 170, 110)))
    draw = ImageDraw.Draw(img)
    for i in range(16):
        a = 2 * math.pi * i / 16
        draw.polygon(
            [
                (w / 2, h * 0.5),
                (w / 2 + w * 1.2 * math.cos(a), h * 0.5 + w * 1.2 * math.sin(a)),
                (w / 2 + w * 1.2 * math.cos(a + 0.12), h * 0.5 + w * 1.2 * math.sin(a + 0.12)),
            ],
            fill=(255, 210, 150),
        )
    rng = random.Random(5)
    for _ in range(26):
        x, y, s = rng.uniform(0, w), rng.uniform(h * 0.15, h * 0.7), w * rng.uniform(0.012, 0.022)
        draw.ellipse((x - s, y - s, x + s, y + s), fill=rng.choice([(255, 255, 255), (222, 92, 72), CARAMEL]))
    draw.ellipse((w * 0.12, h * 0.83, w * 0.88, h * 0.92), fill=(214, 140, 90))
    _bottle(draw, w * 0.34, h * 0.88, h * 0.28)
    _bottle(draw, w * 0.66, h * 0.88, h * 0.28, label=MILK)


PAINTERS = {"sunrise": _sunrise, "slow": _slow, "glass": _glass, "commute": _commute, "offer": _offer}


def paint(scene: str, size: tuple[int, int]) -> Image.Image:
    """画一张示例插画（RGB）。"""
    big = Image.new("RGB", (size[0] * SCALE, size[1] * SCALE))
    PAINTERS[scene](big)
    return big.filter(ImageFilter.GaussianBlur(0.6)).resize(size, Image.LANCZOS)


# ---- 建项目 -----------------------------------------------------------------


def build(store: ProjectStore, *, language: str = "zh-CN", tts: str | None = None) -> Project:
    """建一个示例项目并保存，返回项目。插画按 9:16 画布尺寸现画。"""
    texts, shots = _texts(language)
    project = Project(title=texts["title"], subject=texts["subject"], video_type="product_ad")
    project.canvas = Canvas(aspect="9:16")
    if tts:
        project.voice = VoiceSettings(provider=tts)
    localize.apply_language(project, language)
    project.look = Look(scene="", style=texts["style"], confirmed=True)
    project.titles.headline.enabled = True
    project.titles.headline.text = texts["headline"]
    project = store.create(project)
    size = (project.canvas.width, project.canvas.height)
    with tempfile.TemporaryDirectory(prefix="shotwright-sample-") as work:
        for index, item in enumerate(shots, start=1):
            path = Path(work) / f"sample-{index}.jpg"
            paint(item.scene, size).save(path, quality=90)
            asset = store.add_asset(
                project, path, kind="image", source="upload", width=size[0], height=size[1], folder="upload"
            )
            project.shots.append(
                Shot(
                    text=item.text,
                    title=item.title,
                    visual=ShotVisual(
                        mode="upload", candidates=[asset.id], selected=asset.id, camera=item.camera
                    ),
                )
            )
    project.script = [item.text for item in shots]
    # 价格和购买引导只在最后一个镜头出现：按估算的配音时长定时间，渲染时以实际配音为准。
    from shotwright.services.durations import estimated_shot_seconds

    last_start = round(sum(estimated_shot_seconds(project, shot) for shot in project.shots[:-1]), 1)
    project.overlays = [
        Overlay(text=texts["price"], position="top", start=last_start, size=84, color="#FFE45C", margin=560),
        Overlay(text=texts["cta"], position="middle", start=last_start + 0.8, size=60),
    ]
    return store.save(project)
