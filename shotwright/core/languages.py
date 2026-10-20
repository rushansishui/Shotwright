"""视频内容支持的语言：给大模型的语言名、可选的配音声音、默认字幕字体。

语言代码使用 BCP-47（例如 ``zh-CN``、``en-US``）。新增一种语言只需要在
:data:`LANGUAGES` 里加一行；字体缺字时渲染会自动换用能显示的字体。
"""

from __future__ import annotations

from dataclasses import dataclass

SC_FONT = "NotoSansSC-Bold.ttf"
KR_FONT = "NotoSansKR-Bold.ttf"
THAI_FONT = "NotoSansThai-Bold.ttf"


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    """语言自己的写法，界面上显示。"""
    name_zh: str
    prompt_name: str
    """写进大模型提示词里的语言名。"""
    voices: tuple[tuple[str, str], ...]
    """Edge 配音声音 (id, 说明)，第一个是默认声音。"""
    font: str = SC_FONT

    @property
    def default_voice(self) -> str:
        return self.voices[0][0]


LANGUAGES: dict[str, Language] = {
    lang.code: lang
    for lang in (
        Language(
            "zh-CN",
            "简体中文",
            "简体中文",
            "简体中文",
            (
                ("zh-CN-XiaoxiaoNeural", "晓晓 · 女声"),
                ("zh-CN-YunxiNeural", "云希 · 男声"),
                ("zh-CN-YunjianNeural", "云健 · 男声（有力）"),
                ("zh-CN-XiaoyiNeural", "晓伊 · 女声（活泼）"),
            ),
        ),
        Language(
            "zh-TW",
            "繁體中文",
            "繁体中文（台湾）",
            "繁體中文（台灣用語）",
            (("zh-TW-HsiaoChenNeural", "曉臻 · 女聲"), ("zh-TW-YunJheNeural", "雲哲 · 男聲")),
        ),
        Language(
            "zh-HK",
            "粵語",
            "粤语（香港）",
            "香港粵語口語（繁體字）",
            (("zh-HK-HiuGaaiNeural", "曉佳 · 女聲"), ("zh-HK-WanLungNeural", "雲龍 · 男聲")),
        ),
        Language(
            "en-US",
            "English (US)",
            "英语（美国）",
            "American English",
            (
                ("en-US-JennyNeural", "Jenny · Female"),
                ("en-US-GuyNeural", "Guy · Male"),
                ("en-US-AriaNeural", "Aria · Female"),
            ),
        ),
        Language(
            "en-GB",
            "English (UK)",
            "英语（英国）",
            "British English",
            (("en-GB-SoniaNeural", "Sonia · Female"), ("en-GB-RyanNeural", "Ryan · Male")),
        ),
        Language(
            "ja-JP",
            "日本語",
            "日语",
            "日本語",
            (("ja-JP-NanamiNeural", "七海 · 女性"), ("ja-JP-KeitaNeural", "圭太 · 男性")),
        ),
        Language(
            "ko-KR",
            "한국어",
            "韩语",
            "한국어",
            (("ko-KR-SunHiNeural", "선히 · 여성"), ("ko-KR-InJoonNeural", "인준 · 남성")),
            KR_FONT,
        ),
        Language(
            "es-ES",
            "Español",
            "西班牙语",
            "español",
            (("es-ES-ElviraNeural", "Elvira · Mujer"), ("es-ES-AlvaroNeural", "Álvaro · Hombre")),
        ),
        Language(
            "fr-FR",
            "Français",
            "法语",
            "français",
            (("fr-FR-DeniseNeural", "Denise · Femme"), ("fr-FR-HenriNeural", "Henri · Homme")),
        ),
        Language(
            "de-DE",
            "Deutsch",
            "德语",
            "Deutsch",
            (("de-DE-KatjaNeural", "Katja · Frau"), ("de-DE-ConradNeural", "Conrad · Mann")),
        ),
        Language(
            "pt-BR",
            "Português (Brasil)",
            "葡萄牙语（巴西）",
            "português do Brasil",
            (("pt-BR-FranciscaNeural", "Francisca · Mulher"), ("pt-BR-AntonioNeural", "Antônio · Homem")),
        ),
        Language(
            "ru-RU",
            "Русский",
            "俄语",
            "русский язык",
            (("ru-RU-SvetlanaNeural", "Светлана · Женский"), ("ru-RU-DmitryNeural", "Дмитрий · Мужской")),
        ),
        Language(
            "it-IT",
            "Italiano",
            "意大利语",
            "italiano",
            (("it-IT-ElsaNeural", "Elsa · Donna"), ("it-IT-DiegoNeural", "Diego · Uomo")),
        ),
        Language(
            "vi-VN",
            "Tiếng Việt",
            "越南语",
            "tiếng Việt",
            (("vi-VN-HoaiMyNeural", "Hoài My · Nữ"), ("vi-VN-NamMinhNeural", "Nam Minh · Nam")),
        ),
        Language(
            "id-ID",
            "Bahasa Indonesia",
            "印尼语",
            "Bahasa Indonesia",
            (("id-ID-GadisNeural", "Gadis · Perempuan"), ("id-ID-ArdiNeural", "Ardi · Laki-laki")),
        ),
        Language(
            "th-TH",
            "ไทย",
            "泰语",
            "ภาษาไทย",
            (("th-TH-PremwadeeNeural", "เปรมวดี · หญิง"), ("th-TH-NiwatNeural", "นิวัฒน์ · ชาย")),
            THAI_FONT,
        ),
    )
}

DEFAULT_LANGUAGE = "zh-CN"

# 旧版本项目里保存的是自由文本（例如"简体中文"）或 "auto"。
_ALIASES = {
    "auto": "zh-CN",
    "zh": "zh-CN",
    "简体中文": "zh-CN",
    "中文": "zh-CN",
    "繁體中文": "zh-TW",
    "繁体中文": "zh-TW",
    "粤语": "zh-HK",
    "粵語": "zh-HK",
    "english": "en-US",
    "en": "en-US",
    "英语": "en-US",
    "日本語": "ja-JP",
    "日语": "ja-JP",
    "ja": "ja-JP",
    "한국어": "ko-KR",
    "韩语": "ko-KR",
    "ko": "ko-KR",
}


def normalize(code: str | None) -> str:
    """把各种写法规范成目录里的语言代码；不认识时返回默认语言。"""
    if not code:
        return DEFAULT_LANGUAGE
    if code in LANGUAGES:
        return code
    lowered = code.strip().lower()
    for key in LANGUAGES:
        if key.lower() == lowered:
            return key
    if lowered in _ALIASES or code.strip() in _ALIASES:
        return _ALIASES.get(lowered) or _ALIASES[code.strip()]
    prefix = lowered.split("-")[0]
    for key in LANGUAGES:
        if key.lower().startswith(prefix + "-"):
            return key
    return DEFAULT_LANGUAGE


def get(code: str | None) -> Language:
    return LANGUAGES[normalize(code)]


def voice_language(voice: str) -> str | None:
    """Edge 声音 id 的语言，例如 ``en-US-JennyNeural`` → ``en-US``。"""
    for lang in LANGUAGES.values():
        if any(voice == item[0] for item in lang.voices):
            return lang.code
    parts = voice.split("-")
    if len(parts) >= 3:
        return normalize(f"{parts[0]}-{parts[1]}")
    return None
