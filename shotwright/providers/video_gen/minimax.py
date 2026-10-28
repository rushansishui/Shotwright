"""MiniMax 海螺：文生视频 / 首帧 / 首尾帧 / 参考图生视频（收费）。

接口文档：https://platform.minimax.io/docs/api-reference/video-generation-i2v
（国内站：https://platform.minimaxi.com）

两代接口：

* **Hailuo 2.3 / 02（v1）**：``POST /v1/video_generation``，字段 ``first_frame_image``、
  ``last_frame_image``；查询 ``GET /v1/query/video_generation?task_id=``，成功后拿 ``file_id``
  再 ``GET /v1/files/retrieve?file_id=`` 换下载地址。注意：参数错误、余额不足时 HTTP 仍是 200，
  错误写在 ``base_resp.status_code`` 里。
* **H3（v2）**：``POST /v2/video_generation``，图片放在 ``content`` 数组里，用 ``role`` 区分首帧
  （first_frame）和参考图（reference_image，最多 9 张），首帧和参考图不能同时用；H3 每条视频
  都带声音（镜头默认不播原声，不影响成片）。v2 的查询接口按公开资料实现，
  如果返回格式不同，可用 ``query_path`` 调整。

图片用 ``data:`` 开头的 base64 传，最大 20MB。
"""

from __future__ import annotations

from pathlib import Path

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderError, ProviderSpec
from shotwright.providers.video_gen.common import (
    USD_TO_CNY,
    Request,
    TaskVideoProvider,
    VideoModel,
    billing_for,
    data_url,
    find_url,
    pricing_for,
    resolutions_for,
)

LABEL = "MiniMax 海螺"
DEFAULT_BASE_URL = "https://api.minimaxi.com"
DEFAULT_MODEL = "MiniMax-Hailuo-2.3"
DEFAULT_V2_QUERY = "/v2/query/video_generation/{task_id}"
MAX_IMAGE = 20 * 1024 * 1024
_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")

# 参考价（元 / 秒）：H3 按公开报价 $0.08/秒（768P）、$0.13/秒（2K）折算；Hailuo 2.3 / 02 为估算值，
# 请在 config.toml 里用 price 填自己账户的实际单价。
CATALOG: dict[str, VideoModel] = {
    "MiniMax-Hailuo-2.3": VideoModel(
        "MiniMax-Hailuo-2.3",
        frozenset({"text", "first_frame"}),
        durations=(6, 10),
        min_duration=6,
        max_duration=10,
        resolutions=("768p", "1080p"),
        price={"768p": 0.35, "1080p": 0.6},
    ),
    "MiniMax-Hailuo-2.3-Fast": VideoModel(
        "MiniMax-Hailuo-2.3-Fast",
        frozenset({"first_frame"}),
        durations=(6, 10),
        min_duration=6,
        max_duration=10,
        resolutions=("768p", "1080p"),
        price={"768p": 0.25, "1080p": 0.45},
    ),
    "MiniMax-Hailuo-02": VideoModel(
        "MiniMax-Hailuo-02",
        frozenset({"text", "first_frame", "first_last"}),
        durations=(6, 10),
        min_duration=6,
        max_duration=10,
        resolutions=("512p", "768p", "1080p"),
        price={"512p": 0.2, "768p": 0.35, "1080p": 0.6},
    ),
    "MiniMax-H3": VideoModel(
        "MiniMax-H3",
        frozenset({"text", "first_frame", "first_last", "references"}),
        min_duration=4,
        max_duration=15,
        resolutions=("768p", "2k"),
        max_references=9,
        audio="always",
        price={"768p": round(0.08 * USD_TO_CNY, 3), "2k": round(0.13 * USD_TO_CNY, 3)},
        aspects=_RATIOS,
    ),
    # H3 的其他型号（官方文档）：Highspeed 只有 768P；Lite、Max 是 480P / 768P，Max 最短 5 秒。
    # 价格未单独公布，按 H3 同档估算（宁可估高），请用 price 填实际单价。
    "MiniMax-H3-Highspeed": VideoModel(
        "MiniMax-H3-Highspeed",
        frozenset({"text", "first_frame", "first_last", "references"}),
        min_duration=4,
        max_duration=15,
        resolutions=("768p",),
        max_references=9,
        audio="always",
        price={"768p": round(0.08 * USD_TO_CNY, 3)},
        aspects=_RATIOS,
    ),
    "MiniMax-H3-Lite": VideoModel(
        "MiniMax-H3-Lite",
        frozenset({"text", "first_frame", "first_last", "references"}),
        min_duration=4,
        max_duration=15,
        resolutions=("480p", "768p"),
        max_references=9,
        audio="always",
        price={"480p": round(0.05 * USD_TO_CNY, 3), "768p": round(0.08 * USD_TO_CNY, 3)},
        aspects=_RATIOS,
    ),
    "MiniMax-H3-Max": VideoModel(
        "MiniMax-H3-Max",
        frozenset({"text", "first_frame", "first_last", "references"}),
        min_duration=5,
        max_duration=15,
        resolutions=("480p", "768p"),
        max_references=9,
        audio="always",
        price={"480p": round(0.05 * USD_TO_CNY, 3), "768p": round(0.08 * USD_TO_CNY, 3)},
        aspects=_RATIOS,
    ),
}


def _resolution_value(value: str) -> str:
    """768p → 768P，2k → 2K。"""
    return value.upper()


def _status(body: dict) -> str:
    """v1 的状态在顶层 ``status``；v2 可能放在 ``task`` 或 ``data`` 里。"""
    for holder in (body, body.get("task"), body.get("data")):
        if isinstance(holder, dict) and holder.get("status"):
            return str(holder["status"])
    return ""


def _reject(body: dict) -> str | None:
    base = body.get("base_resp")
    if isinstance(base, dict) and base.get("status_code") not in (None, 0, "0"):
        return f"{base.get('status_code')}: {base.get('status_msg') or ''}"
    return None


class MiniMaxVideo(TaskVideoProvider):
    LABEL = LABEL
    CATALOG = CATALOG
    DEFAULT_MODEL = DEFAULT_MODEL
    MAX_PROMPT = 2000
    CONSOLE = "MiniMax 开放平台控制台"

    def __init__(self, settings: dict[str, str]):
        super().__init__(settings)
        self.api_key = settings["api_key"]
        self.base_url = (settings.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.v2 = self.model.upper().startswith("MINIMAX-H3")
        self.query_path = settings.get("query_path") or DEFAULT_V2_QUERY
        # MiniMax 默认会改写提示词；镜匠的提示词里有颜色、款式等硬性要求，默认不让它改写。
        self.prompt_optimizer = str(settings.get("prompt_optimizer") or "").strip().lower() in {"1", "true", "yes", "on"}

    @property
    def max_duration(self) -> int:
        """Hailuo 的 1080P 只有 6 秒一档（更长的镜头由镜匠分段续生成）；草稿档降到 768P 后又能用 10 秒。"""
        if self.resolution == "1080p" and not getattr(self, "v2", False):
            return min(self._max_duration, 6)
        return self._max_duration

    @max_duration.setter
    def max_duration(self, value: int) -> None:
        self._max_duration = value

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def build_payload(self, request: Request) -> dict:
        if self.v2:
            content: list[dict] = [{"type": "text", "text": request.prompt}]
            if request.first_frame is not None:
                content.append(
                    {"type": "image_url", "image_url": {"url": data_url(request.first_frame, limit=MAX_IMAGE)}, "role": "first_frame"}
                )
            for path in request.references:
                content.append(
                    {"type": "image_url", "image_url": {"url": data_url(path, limit=MAX_IMAGE)}, "role": "reference_image"}
                )
            ratio = "adaptive" if request.first_frame is not None or request.aspect not in _RATIOS else request.aspect
            return {
                "model": self.model,
                "content": content,
                "duration": request.seconds,
                "resolution": _resolution_value(self.resolution),
                "ratio": ratio,
            }
        body: dict = {
            "model": self.model,
            "prompt": request.prompt,
            "duration": request.seconds,
            "resolution": _resolution_value(self.resolution),
            "prompt_optimizer": self.prompt_optimizer,
        }
        if request.first_frame is not None:
            body["first_frame_image"] = data_url(request.first_frame, limit=MAX_IMAGE)
        return body

    def _submit(self, client, request: Request) -> str:
        task_id = self._paid_task.submit(
            client,
            f"{self.base_url}/{'v2' if self.v2 else 'v1'}/video_generation",
            self.build_payload(request),
            headers=self._headers,
            label=LABEL,
            secret=self.api_key,
            task_id_of=lambda body: body.get("task_id") or (body.get("data") or {}).get("task_id"),
            reject_of=_reject,
            console=self.CONSOLE,
        )
        return f"{'v2' if self.v2 else 'v1'}:{task_id}"

    def _finish(self, client, task_id: str, output: Path) -> Path:
        version, _, remote_id = task_id.partition(":")
        if not remote_id:
            version, remote_id = ("v2" if self.v2 else "v1"), task_id
        if version == "v2":
            url = f"{self.base_url}{self.query_path.format(task_id=remote_id)}"
        else:
            url = f"{self.base_url}/v1/query/video_generation?task_id={remote_id}"
        body = self._paid_task.poll(
            client,
            url,
            task_id=remote_id,
            headers=self._headers,
            secret=self.api_key,
            settings=self._poll_settings(
                success=frozenset({"success", "succeeded", "succeed", "completed"}),
                active=frozenset({"preparing", "queueing", "queued", "processing", "pending", "running", "submitted"}),
                failure=frozenset({"fail", "failed", "expired", "canceled", "cancelled"}),
                status_of=_status,
                error_of=lambda reply: (reply.get("base_resp") or {}).get("status_msg") or reply.get("error"),
            ),
        )
        video_url = find_url(body)
        if not video_url:
            file_id = body.get("file_id") or (body.get("task") or {}).get("file_id")
            if not file_id:
                raise ProviderError(f"{LABEL} 任务完成但没有返回视频文件，任务 id：{remote_id}")
            response = client.get(f"{self.base_url}/v1/files/retrieve", params={"file_id": file_id}, headers=self._headers)
            video_url = find_url(response.json() if response.status_code < 300 else {})
        return self._paid_task.download(client, video_url, output.with_suffix(".mp4"), label=LABEL, task_id=remote_id)


registry.register(
    ProviderSpec(
        kind="video_gen",
        id="minimax",
        name="MiniMax 海螺",
        name_en="MiniMax Hailuo",
        fields=(
            ConfigField("api_key", "API Key", required=True, secret=True, help="留空时使用 MiniMax 配音的 Key"),
            ConfigField(
                "model",
                "模型",
                default=DEFAULT_MODEL,
                help="MiniMax-Hailuo-2.3 / MiniMax-Hailuo-2.3-Fast（仅图生）/ MiniMax-Hailuo-02 / "
                "MiniMax-H3（支持参考图，另有 -Highspeed / -Lite / -Max）",
            ),
            ConfigField(
                "base_url", "接口地址", default=DEFAULT_BASE_URL, help="国内 https://api.minimaxi.com，国际 https://api.minimax.io"
            ),
            ConfigField("resolution", "分辨率", default="768p", help="Hailuo：512p / 768p / 1080p（1080p 只有 6 秒）；H3：768p / 2k"),
            ConfigField("draft_resolution", "草稿分辨率", default="512p"),
            ConfigField("max_duration", "最长秒数", help="留空按模型上限"),
            ConfigField("prompt_optimizer", "让 MiniMax 改写提示词", default="false"),
            ConfigField("query_path", "H3 查询路径", default=DEFAULT_V2_QUERY),
            ConfigField("price", "单价（元/秒）", help="留空用参考价"),
            ConfigField("poll_interval", "轮询间隔（秒）", default="10"),
            ConfigField("run_timeout", "最长等待（秒）", default="1800"),
        ),
        capabilities=frozenset({"text_to_video", "first_frame", "reference_images"}),
        paid=True,
        docs_url="https://platform.minimax.io/docs/api-reference/video-generation-i2v",
        shared_key_from=(("tts", "minimax"),),
        max_duration=15.0,
        has_audio=False,
        max_ref_images=9,
        prompt_languages=("zh", "en"),
        pricing=pricing_for(CATALOG, DEFAULT_MODEL),
        billing=billing_for(CATALOG, DEFAULT_MODEL),
        resolutions_of=resolutions_for(CATALOG, DEFAULT_MODEL),
    ),
    MiniMaxVideo,
)