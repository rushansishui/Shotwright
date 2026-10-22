"""火山方舟 Seedance：文生视频 / 首帧图生视频（收费）。

接口文档：https://www.volcengine.com/docs/82379/1520757
与豆包大模型用同一个方舟 API Key：本节没填 ``api_key`` 时，会借用
``[providers.llm.doubao]`` 里的 Key。

参考 MoneyPrinterTurbo ``app/services/video_providers/seedance.py``（MIT）的请求格式。
生成结果的下载地址约 24 小时后失效，因此生成成功后立即下载到项目素材里。
"""

from __future__ import annotations

import base64
import math
from collections.abc import Callable
from pathlib import Path

import httpx

from shotwright.providers import paid_task, registry
from shotwright.providers.base import ConfigField, ProviderError, ProviderSpec
from shotwright.providers.paid_task import sniff_image_type

LABEL = "Seedance"
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
MAX_FIRST_FRAME = 30 * 1024 * 1024
_RATIOS = {"9:16": "9:16", "16:9": "16:9", "1:1": "1:1"}
RESOLUTIONS = ("480p", "720p", "1080p")


def _flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def clamp_duration(seconds: float, minimum: int, maximum: int) -> int:
    """把镜头时长向上取整到整秒，再限制在模型支持的范围内。"""
    wanted = math.ceil(max(seconds, 0.1) - 1e-6)
    return max(minimum, min(maximum, wanted))


def image_data_url(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_FIRST_FRAME:
        raise ProviderError("首帧图片超过 30MB")
    data = path.read_bytes()
    mime = sniff_image_type(data) or ""
    if not mime.startswith("image/"):
        raise ProviderError("首帧必须是 PNG、JPEG、WebP 等常见图片格式")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


class SeedanceVideo:
    def __init__(self, settings: dict[str, str]):
        self.api_key = settings["api_key"]
        self.base_url = (settings.get("base_url") or ARK_BASE_URL).rstrip("/")
        self.model = settings.get("model") or "doubao-seedance-2-5-260628"
        self.resolution = (settings.get("resolution") or "1080p").strip().lower()
        if self.resolution not in RESOLUTIONS:
            # 分辨率决定价格，填错时直接报错，不悄悄换成别的档位。
            raise ValueError(f"resolution 只能是 {' / '.join(RESOLUTIONS)}")
        self.min_duration = int(settings.get("min_duration") or 4)
        self.max_duration = int(settings.get("max_duration") or 30)
        if not 1 <= self.min_duration <= self.max_duration:
            raise ValueError("min_duration 必须大于 0 且不大于 max_duration")
        self.watermark = _flag(settings.get("watermark"))
        self.generate_audio = _flag(settings.get("generate_audio"))
        self.poll = paid_task.PollSettings(
            label=LABEL,
            interval=float(settings.get("poll_interval") or 5),
            timeout=float(settings.get("run_timeout") or 1800),
            error_of=lambda body: (
                (body.get("error") or {}).get("message")
                if isinstance(body.get("error"), dict)
                else body.get("error")
            ),
        )
        self.proxy = settings.get("proxy") or None
        self.timeout = float(settings.get("timeout") or 60)

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, proxy=self.proxy, headers={"User-Agent": "Shotwright/0.2"})

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def build_payload(self, prompt: str, *, aspect: str, duration: float, first_frame: Path | None) -> dict:
        content: list[dict] = [{"type": "text", "text": prompt.strip()}]
        if first_frame is not None:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url(first_frame)},
                    "role": "first_frame",
                }
            )
        return {
            "model": self.model,
            "content": content,
            # 图生视频时按首帧比例生成，渲染时再按画布裁切。
            "ratio": "adaptive" if first_frame is not None else _RATIOS.get(aspect, "9:16"),
            "duration": clamp_duration(duration, self.min_duration, self.max_duration),
            "resolution": self.resolution,
            "watermark": self.watermark,
            "generate_audio": self.generate_audio,
        }

    def generate(
        self,
        prompt: str,
        *,
        aspect: str,
        duration: float,
        output: Path,
        first_frame: Path | None = None,
        on_task: Callable[[str], None] | None = None,
    ) -> Path:
        if not prompt.strip():
            raise ProviderError("缺少画面提示词")
        payload = self.build_payload(prompt, aspect=aspect, duration=duration, first_frame=first_frame)
        tasks_url = f"{self.base_url}/contents/generations/tasks"
        with self._client() as client:
            task_id = paid_task.submit(
                client, tasks_url, payload, headers=self._headers, label=LABEL, secret=self.api_key
            )
            if on_task is not None:
                on_task(task_id)
            return self._finish(client, task_id, output)

    def resume(self, task_id: str, *, output: Path) -> Path:
        """继续轮询一个已提交的任务（进程重启后恢复用）。"""
        tasks_url = f"{self.base_url}/contents/generations/tasks"
        with self._client() as client:
            return self._finish(client, task_id, output, tasks_url=tasks_url)

    def _finish(self, client: httpx.Client, task_id: str, output: Path, *, tasks_url: str | None = None) -> Path:
        base = tasks_url or f"{self.base_url}/contents/generations/tasks"
        body = paid_task.poll(
            client,
            f"{base}/{task_id}",
            task_id=task_id,
            headers=self._headers,
            secret=self.api_key,
            settings=self.poll,
        )
        video_url = str(((body.get("content") or {}).get("video_url")) or "")
        return paid_task.download(
            client, video_url, output.with_suffix(".mp4"), label=LABEL, task_id=task_id
        )


registry.register(
    ProviderSpec(
        kind="video_gen",
        id="seedance",
        name="Seedance（火山方舟）",
        name_en="Seedance (Volcengine Ark)",
        fields=(
            ConfigField(
                "api_key", "方舟 API Key", required=True, secret=True, help="留空时使用豆包大模型的 Key"
            ),
            ConfigField("model", "模型", default="doubao-seedance-2-5-260628"),
            ConfigField("base_url", "Base URL", default=ARK_BASE_URL),
            ConfigField("resolution", "分辨率", default="1080p", help="480p / 720p / 1080p"),
            ConfigField("min_duration", "最短秒数", default="4"),
            ConfigField("max_duration", "最长秒数", default="30"),
            ConfigField("poll_interval", "轮询间隔（秒）", default="5"),
            ConfigField("run_timeout", "最长等待（秒）", default="1800"),
            ConfigField("watermark", "加水印", default="false"),
            ConfigField("generate_audio", "生成音效", default="false"),
        ),
        capabilities=frozenset({"text_to_video", "first_frame"}),
        paid=True,
        docs_url="https://www.volcengine.com/docs/82379/1520757",
        shared_key_from=(("llm", "doubao"),),
        max_duration=30.0,
        has_audio=False,
        max_ref_images=1,
    ),
    SeedanceVideo,
)
