"""生成 GitHub 社交预览图（1280×640）。

用法（仓库根目录）：

    python scripts/make_social_preview.py

输出 ``docs/images/social-preview.png``。改文案或换截图后重跑一次即可。
GitHub 的社交预览图要手动上传：仓库 → Settings → Social preview。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT = ROOT / "docs" / "images" / "zh-home.png"
OUTPUT = ROOT / "docs" / "images" / "social-preview.png"
FONT_BOLD = ROOT / "resources" / "fonts" / "NotoSansSC-Bold.ttf"
FONT_REGULAR = ROOT / "resources" / "fonts" / "NotoSansSC-Regular.otf"

W, H = 1280, 640
BG_TOP = (22, 18, 26)
BG_BOTTOM = (46, 30, 22)
ACCENT = (240, 163, 94)
WHITE = (250, 247, 243)
MUTED = (186, 174, 162)


def _gradient(size: tuple[int, int]) -> Image.Image:
    column = Image.new("RGB", (1, size[1]))
    for y in range(size[1]):
        k = y / max(1, size[1] - 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * k) for a, b in zip(BG_TOP, BG_BOTTOM, strict=True)))
    return column.resize(size)


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _shadowed_card(image: Image.Image, size: tuple[int, int], radius: int = 18) -> Image.Image:
    """把截图缩成圆角卡片，下面加一层柔和阴影。"""
    card = image.convert("RGB").resize(size, Image.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *size), radius=radius, fill=255)
    card.putalpha(mask)

    shadow = Image.new("RGBA", (size[0] + 60, size[1] + 60), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (30, 30, 30 + size[0], 30 + size[1]), radius=radius, fill=(0, 0, 0, 150)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    return card, shadow


def main() -> None:
    canvas = _gradient((W, H)).convert("RGBA")
    draw = ImageDraw.Draw(canvas)

    # 左侧暖色竖条
    draw.rounded_rectangle((72, 120, 80, 360), radius=4, fill=ACCENT)

    title = _font(FONT_BOLD, 64)
    title_cn = _font(FONT_BOLD, 38)
    subtitle = _font(FONT_REGULAR, 30)
    small = _font(FONT_REGULAR, 22)
    tiny = _font(FONT_REGULAR, 21)

    draw.text((108, 104), "Shotwright", font=title, fill=WHITE)
    draw.text((108 + draw.textlength("Shotwright", font=title) + 18, 126), "镜匠", font=title_cn, fill=ACCENT)

    draw.text((108, 204), "一份脚本，变成一组能投放的广告。", font=subtitle, fill=WHITE)
    draw.text((108, 272), "镜头级控制 · 改一个镜头只重渲这个镜头", font=small, fill=MUTED)
    draw.text((108, 308), "开源 MIT · 本地运行 · 不填 Key 也能先出片", font=small, fill=MUTED)
    draw.text((108, 344), "逐镜头选画面 · 配音字幕音乐自动完成", font=small, fill=MUTED)
    draw.text((108, 380), "一键出多语言版本 · 每笔花费都看得见", font=small, fill=MUTED)

    # 右侧截图卡片
    if SCREENSHOT.is_file():
        shot = Image.open(SCREENSHOT)
        card_w = 560
        card_h = round(card_w * shot.height / shot.width)
        card, shadow = _shadowed_card(shot, (card_w, card_h))
        x, y = 680, (H - card_h) // 2
        canvas.alpha_composite(shadow, (x - 30, y - 30))
        canvas.alpha_composite(card, (x, y))
        draw.rounded_rectangle(
            (x, y, x + card_w, y + card_h), radius=18, outline=(255, 255, 255, 40), width=2
        )

    draw.text((108, 540), "github.com/rushansishui/Shotwright", font=tiny, fill=MUTED)
    draw.rounded_rectangle((104, 528, 112, 536), radius=2, fill=ACCENT)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(OUTPUT, optimize=True)
    print(f"已生成 {OUTPUT.relative_to(ROOT)}（{W}×{H}）")


if __name__ == "__main__":
    main()
