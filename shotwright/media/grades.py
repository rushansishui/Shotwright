"""统一调色预设：渲染时对整片做一次调色，让不同来源的素材色调一致。

调色在字幕和叠加文字之前做，文字颜色不受影响。参数都比较克制，
目的是把素材库、AI 生成和手机原片拉到同一个色调，而不是做强烈的风格化。
"""

from __future__ import annotations

GRADES: dict[str, str] = {
    "natural": "eq=contrast=1.04:saturation=1.06",
    "warm": "colorbalance=rs=0.04:bs=-0.04:rm=0.05:bm=-0.04:rh=0.03:bh=-0.03,eq=saturation=1.05",
    "cool": "colorbalance=rs=-0.03:bs=0.04:rm=-0.03:bm=0.05,eq=saturation=1.02",
    "vivid": "eq=contrast=1.08:saturation=1.28",
    "film": "curves=master='0/0.05 0.25/0.24 0.75/0.78 1/0.95',eq=saturation=0.9,colorbalance=rh=0.03:bh=-0.03",
    "bw": "hue=s=0,eq=contrast=1.12",
}
"""调色预设名 → ffmpeg 滤镜。改动参数时同步修改 GRADE_VERSION，让渲染缓存失效。"""

GRADE_VERSION = "grade-1"


def grade_filter(name: str) -> str:
    """预设对应的滤镜；空字符串或未知预设表示不调色。"""
    return GRADES.get(name, "")
