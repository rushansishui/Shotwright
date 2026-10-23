"""渲染前的费用预估。

根据镜头的画面来源、配音服务和当前 Provider 设置，给出大致积分/费用区间。
数字仅供参考，实际扣费以平台为准。

目前覆盖的计费规则：
- Seedance 视频生成：按秒计费，参考方舟价格
- Seedream / OpenAI 图片生成：按张计费
- 付费配音服务（Azure / 硅基流动 / MiniMax / ElevenLabs）：按字符估算

单位统一用"积分"（方舟积分）或"元"（CNY）中的一种；
这里用人民币分（0.001 元精度）方便对比。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shotwright.config import Settings
from shotwright.core.models import Project, Shot
from shotwright.providers.video_gen.seedance import clamp_duration
from shotwright.services.durations import estimated_shot_seconds

# ---- 参考单价（随时更新） -----------------------------------------------
# 视频生成：元 / 秒
_VIDEO_GEN_PRICE: dict[str, float] = {
    "seedance": 0.14,   # 方舟 Seedance 2.5，参考：¥0.14/秒
}
# 视频分辨率相对 1080p 的价格系数（参考方舟各档价格的大致比例）
_RESOLUTION_FACTOR: dict[str, float] = {"480p": 0.25, "720p": 0.5, "1080p": 1.0}
# 图片生成：元 / 张
_IMAGE_GEN_PRICE: dict[str, float] = {
    "seedream": 0.06,   # 方舟 Seedream，参考：¥0.06/张
    "openai": 0.04,     # OpenAI gpt-image-1，参考：$0.04/张 ≈ ¥0.28，此处用低档
}
# 付费配音：元 / 千字符
_TTS_PRICE_PER_1K: dict[str, float] = {
    "azure": 0.06,        # Azure 标准声音
    "siliconflow": 0.03,  # 硅基流动
    "minimax": 0.05,      # MiniMax
    "elevenlabs": 0.12,   # ElevenLabs multilingual
}


@dataclass
class ShotCostItem:
    shot_index: int
    shot_id: str
    visual_cost: float
    """画面生成费用（元）。"""
    tts_cost: float
    """配音费用（元）。"""
    note: str = ""


@dataclass
class CostEstimate:
    items: list[ShotCostItem] = field(default_factory=list)
    total_visual: float = 0.0
    total_tts: float = 0.0
    currency: str = "CNY"

    @property
    def total(self) -> float:
        return round(self.total_visual + self.total_tts, 4)

    def to_dict(self) -> dict:
        return {
            "currency": self.currency,
            "total": self.total,
            "total_visual": round(self.total_visual, 4),
            "total_tts": round(self.total_tts, 4),
            "items": [
                {
                    "shot_index": item.shot_index,
                    "shot_id": item.shot_id,
                    "visual_cost": round(item.visual_cost, 4),
                    "tts_cost": round(item.tts_cost, 4),
                    "note": item.note,
                }
                for item in self.items
            ],
        }


def estimate_render_cost(project: Project, settings: Settings, quality: str = "final") -> CostEstimate:
    """估算渲染整个项目的费用。

    - 已有素材（selected != None）的镜头不重新生成，不计视频费。
    - stock / upload / color 模式不收费。
    - 免费配音（edge / silent）不计 TTS 费。
    """
    estimate = CostEstimate()

    for index, shot in enumerate(project.shots):
        visual_cost, tts_cost, note = 0.0, 0.0, ""
        visual = shot.visual

        # ---- 画面费用 -------------------------------------------------------
        if visual.mode in ("ai", "image") and not visual.selected:
            provider_id = visual.provider or settings.default_provider(
                "video_gen" if visual.mode == "ai" else "image_gen"
            )
            if visual.mode == "ai":
                resolution = _draft_resolution(settings, provider_id) if quality == "draft" else ""
                price = _VIDEO_GEN_PRICE.get(provider_id, 0.0) * resolution_factor(settings, provider_id, resolution)
                if price:
                    duration = _shot_duration(project, shot, settings, provider_id)
                    visual_cost = price * duration
                    note = f"{provider_id} {duration:.0f}s"
            else:
                price = _IMAGE_GEN_PRICE.get(provider_id, 0.0)
                if price:
                    visual_cost = price
                    note = f"{provider_id} 1 image"

        # ---- 配音费用 -------------------------------------------------------
        shot_voice = shot.voice or project.voice
        tts_id = shot_voice.provider
        tts_price = _TTS_PRICE_PER_1K.get(tts_id, 0.0)
        if tts_price and shot.text.strip():
            chars = len(shot.text)
            tts_cost = tts_price * chars / 1000.0

        item = ShotCostItem(index, shot.id, visual_cost, tts_cost, note)
        estimate.items.append(item)
        estimate.total_visual += visual_cost
        estimate.total_tts += tts_cost

    estimate.total_visual = round(estimate.total_visual, 4)
    estimate.total_tts = round(estimate.total_tts, 4)
    return estimate


def _shot_duration(project: Project, shot: Shot, settings: Settings, provider_id: str) -> float:
    """估算 AI 视频的计费秒数。

    Seedance 按整秒、且在配置的上下限内生成（见 ``seedance.clamp_duration``），
    计费也按实际生成的时长算，所以这里同样向上取整并套用上下限。
    """
    seconds = estimated_shot_seconds(project, shot)
    config = settings.provider_settings("video_gen", provider_id)
    try:
        minimum = int(config.get("min_duration") or 4)
        maximum = int(config.get("max_duration") or 30)
    except ValueError:
        minimum, maximum = 4, 30
    return float(clamp_duration(seconds, minimum, max(minimum, maximum)))


def _draft_resolution(settings: Settings, provider_id: str) -> str:
    config = settings.provider_settings("video_gen", provider_id)
    final = str(config.get("resolution") or "1080p").strip().lower()
    draft = str(config.get("draft_resolution") or "720p").strip().lower()
    return draft if _RESOLUTION_FACTOR.get(draft, 1.0) < _RESOLUTION_FACTOR.get(final, 1.0) else final


def resolution_factor(settings: Settings, provider_id: str, resolution: str = "") -> float:
    """按分辨率折算价格；没传分辨率时用配置里的分辨率（默认 1080p）。"""
    chosen = resolution or settings.provider_settings("video_gen", provider_id).get("resolution") or "1080p"
    return _RESOLUTION_FACTOR.get(str(chosen).strip().lower(), 1.0)


def generation_cost(
    settings: Settings, project: Project, shot: Shot, kind: str, provider_id: str, *, resolution: str = ""
) -> float:
    """一次收费生成的参考费用（元），生成成功后累加到项目的 ``cost_credits``。"""
    if kind == "video_gen":
        price = _VIDEO_GEN_PRICE.get(provider_id, 0.0) * resolution_factor(settings, provider_id, resolution)
        return round(price * _shot_duration(project, shot, settings, provider_id), 4) if price else 0.0
    if kind == "image_gen":
        return _IMAGE_GEN_PRICE.get(provider_id, 0.0)
    return 0.0
