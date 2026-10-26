"""字体库：内置字体（resources/fonts）加上用户导入的字体（storage/fonts）。

项目里按文件名引用字体。同一字体族可以有多个字重的文件（例如思源黑体的常规和粗体），
「加粗」开关在同族里挑字重：有粗体文件就用粗体，没有时由 libass 合成加粗；
「倾斜」没有斜体文件时同样由 libass 合成。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

FONT_SUFFIXES = (".ttf", ".otf")
MAX_FONT_BYTES = 40 * 1024 * 1024

LABELS: dict[str, tuple[str, str]] = {
    "Noto Sans SC": ("思源黑体", "Noto Sans SC"),
    "Noto Serif SC": ("思源宋体", "Noto Serif SC"),
    "ZCOOL KuaiLe": ("站酷快乐体", "ZCOOL KuaiLe"),
    "ZCOOL QingKe HuangYou": ("站酷庆科黄油体", "ZCOOL QingKe HuangYou"),
    "Ma Shan Zheng": ("马善政毛笔楷书", "Ma Shan Zheng"),
    "Zhi Mang Xing": ("志莽行书", "Zhi Mang Xing"),
    "Noto Sans KR": ("思源黑体（韩文）", "Noto Sans KR"),
    "Noto Sans Thai": ("Noto Sans（泰文）", "Noto Sans Thai"),
}
"""内置字体的显示名（中文, 英文）；导入的字体用字体自己的族名。"""

FALLBACK_ORDER = ("Noto Sans SC", "Noto Sans KR", "Noto Sans Thai", "Noto Serif SC")
"""缺字时依次尝试的字体族：先找通用的黑体，避免缺字时换成书法、卡通字体。"""


@dataclass(frozen=True)
class FontFace:
    file: str
    path: Path
    family: str
    ass_family: str
    weight: int
    user: bool

    @property
    def bold(self) -> bool:
        return self.weight >= 600


def _name(font, name_id: int) -> str | None:
    """name 表里的名字：英文优先（Windows 英文 0x409 / Mac 英文 0），否则取第一个能读的。"""
    found = None
    for record in font["name"].names:
        if record.nameID != name_id:
            continue
        try:
            text = record.toUnicode().strip()
        except UnicodeDecodeError:
            continue
        if not text:
            continue
        if record.langID in (0x409, 0):
            return text
        found = found or text
    return found


@lru_cache(maxsize=256)
def _read_face(path: str, mtime: float) -> tuple[str, str, int] | None:
    """（字体族名, ASS 用的族名, 字重）；读不出来的文件返回 None。``mtime`` 只用来让缓存随文件更新失效。

    分组用「排版族名」（name 16，同一字体的各字重共用），ASS 里引用用普通族名（name 1），
    libass 一定认得后者。
    """
    from fontTools.ttLib import TTFont, TTLibError

    try:
        with TTFont(path, lazy=True, fontNumber=0) as font:
            legacy = _name(font, 1)
            family = _name(font, 16) or legacy
            weight = int(font["OS/2"].usWeightClass) if "OS/2" in font else 400
            if not family or not legacy or "cmap" not in font:
                return None
            return family, legacy, weight
    except (TTLibError, OSError, KeyError, AssertionError, ValueError, UnicodeDecodeError):
        return None


def user_fonts_dir(settings) -> Path:
    """用户导入的字体放在项目存储目录旁边，不进代码仓库。"""
    return Path(settings.storage_dir).parent / "fonts"


def _dirs(settings) -> list[tuple[Path, bool]]:
    return [(Path(settings.fonts_dir).resolve(), False), (user_fonts_dir(settings).resolve(), True)]


def catalog(settings) -> list[FontFace]:
    """所有可用字体（内置在前）。同名文件以内置字体为准。"""
    faces: list[FontFace] = []
    seen: set[str] = set()
    for directory, user in _dirs(settings):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in FONT_SUFFIXES or path.name in seen or not path.is_file():
                continue
            info = _read_face(str(path), path.stat().st_mtime)
            if info is None:
                continue
            seen.add(path.name)
            faces.append(FontFace(path.name, path, info[0], info[1], info[2], user))
    return faces


def find(settings, name: str) -> FontFace | None:
    return next((face for face in catalog(settings) if face.file == name), None)


def family_faces(settings, family: str) -> list[FontFace]:
    return [face for face in catalog(settings) if face.family == family]


def pick_weight(settings, face: FontFace, bold: bool) -> FontFace:
    """在同一字体族里按「加粗」挑字重最接近的文件（粗体 700，常规 400）。"""
    target = 700 if bold else 400
    faces = family_faces(settings, face.family) or [face]
    return min(faces, key=lambda item: (abs(item.weight - target), item.file != face.file))


def label(family: str, lang: str = "zh") -> str:
    names = LABELS.get(family)
    if not names:
        return family
    return names[0] if lang.startswith("zh") else names[1]


@dataclass
class FontFamily:
    family: str
    name_zh: str
    name_en: str
    file: str
    """选这个字体时写进项目的文件（有常规字重时用常规）。"""
    files: list[str]
    weights: dict[str, int]
    """每个文件的字重（常规 400、粗体 700）。"""
    has_bold: bool
    user: bool


def families(settings) -> list[dict]:
    """按字体族分组的字体列表，给界面的字体选择用。"""
    groups: dict[str, list[FontFace]] = {}
    for face in catalog(settings):
        groups.setdefault(face.family, []).append(face)
    result = []
    for family, faces in groups.items():
        main = min(faces, key=lambda item: abs(item.weight - 400))
        result.append(
            asdict(
                FontFamily(
                    family=family,
                    name_zh=label(family, "zh"),
                    name_en=label(family, "en"),
                    file=main.file,
                    files=[face.file for face in faces],
                    weights={face.file: face.weight for face in faces},
                    has_bold=any(face.bold for face in faces),
                    user=all(face.user for face in faces),
                )
            )
        )
    return result


_SAFE_NAME = re.compile(r"[^\w.\-]+", re.UNICODE)


class FontError(ValueError):
    pass


def import_font(settings, source: Path, filename: str) -> FontFace:
    """导入用户的字体文件（.ttf / .otf）。同名的导入字体会被替换，内置字体不能覆盖。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in FONT_SUFFIXES:
        raise FontError("只支持 .ttf 和 .otf 字体文件")
    if source.stat().st_size > MAX_FONT_BYTES:
        raise FontError("字体文件超过 40MB")
    info = _read_face(str(source), source.stat().st_mtime)
    if info is None:
        raise FontError("无法读取这个字体文件")
    stem = _SAFE_NAME.sub("-", Path(filename).stem).strip(".-") or "font"
    name = f"{stem[:60]}{suffix}"
    if (Path(settings.fonts_dir) / name).exists():
        raise FontError("和内置字体重名，请先改个文件名")
    target_dir = user_fonts_dir(settings)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    target.write_bytes(source.read_bytes())
    return FontFace(name, target, info[0], info[1], info[2], True)


def delete_font(settings, name: str) -> None:
    face = find(settings, name)
    if face is None:
        raise FontError("找不到这个字体")
    if not face.user:
        raise FontError("内置字体不能删除")
    face.path.unlink()
