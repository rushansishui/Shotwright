"""通义万相（阿里云百炼）：文生视频 / 首帧 / 首尾帧 / 参考图生视频（收费）。

接口文档：https://help.aliyun.com/zh/model-studio/video-generate-edit-model/
（万相 3.0：https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-api-reference）

* 异步任务：``POST {base}/services/aigc/video-generation/video-synthesis``（请求头
  ``X-DashScope-Async: enable``），再 ``GET {base}/tasks/{task_id}`` 查询。
* 图片用 ``data:`` 开头的 base64 传；参考图在提示词里按「图 1、图 2」编号，顺序和提交顺序一致。
* 结果链接和任务 id 都只保留 24 小时，生成成功后立即下载。
* 与通义千问大模型用同一把百炼 API Key。地址、模型、Key 必须属于同一地域；新版百炼控制台
  给出的地址形如 ``https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1``。
"""

from __future__ import annotations

from pathlib import Path

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderSpec
from shotwright.providers.video_gen.common import (
    Request,
    TaskVideoProvider,
    VideoModel,
    billing_for,
    data_url,
    pricing_for,
    resolutions_for,
)

LABEL = "通义万相"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_MODEL = "wan3.0-video"
MAX_IMAGE = 20 * 1024 * 1024
_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")

# 参考价（元 / 秒，北京地域原价）：万相 3.0 来自阿里云公告；其他型号未核实，按 3.0 同档估算。
_WAN3_PRICE = {"480p": 0.3, "720p": 0.6, "1080p": 1.2}
CATALOG: dict[str, VideoModel] = {
    "wan3.0-video": VideoModel(
        "wan3.0-video",
        frozenset({"text", "first_frame", "first_last", "references"}),
        min_duration=2,
        max_duration=30,
        resolutions=("480p", "720p", "1080p"),
        max_references=10,
        audio="optional",
        price=_WAN3_PRICE,
        aspects=_RATIOS,
    ),
    "wan3.0-video-prime": VideoModel(
        "wan3.0-video-prime",
        frozenset({"text", "first_frame", "first_last", "references"}),
        min_duration=2,
        max_duration=30,
        resolutions=("480p", "720p", "1080p"),
        max_references=10,
        audio="optional",
        price=_WAN3_PRICE,
        aspects=_RATIOS,
    ),
    "wan2.7-t2v": VideoModel(
        "wan2.7-t2v",
        frozenset({"text"}),
        min_duration=2,
        max_duration=15,
        resolutions=("720p", "1080p"),
        audio="always",
        price={"720p": 0.6, "1080p": 1.2},
    ),
    "wan2.7-i2v": VideoModel(
        "wan2.7-i2v",
        frozenset({"first_frame", "first_last"}),
        min_duration=2,
        max_duration=15,
        resolutions=("720p", "1080p"),
        audio="always",
        price={"720p": 0.6, "1080p": 1.2},
    ),
}


def api_base(url: str) -> str:
    """把 base_url 整理成 ``https://域名/api/v1``：少写 /api/v1、或把完整的提交地址整个贴进来都能用。"""
    url = url.strip().rstrip("/")
    if "{" in url or "}" in url:
        raise ValueError("base_url 里的 {WorkspaceId} 要换成你的业务空间 ID（百炼控制台「业务空间详情」页面）")
    if "/api/v1" in url:
        return url[: url.index("/api/v1") + len("/api/v1")]
    for marker in ("/services/", "/tasks", "/compatible-mode"):
        if marker in url:
            url = url[: url.index(marker)]
    return url.rstrip("/") + "/api/v1"


class WanVideo(TaskVideoProvider):
    LABEL = LABEL
    CATALOG = CATALOG
    DEFAULT_MODEL = DEFAULT_MODEL
    MAX_PROMPT = 5000
    CONSOLE = "阿里云百炼控制台的「模型广场」"

    def __init__(self, settings: dict[str, str]):
        super().__init__(settings)
        self.api_key = settings["api_key"]
        self.base_url = api_base(str(settings.get("base_url") or DEFAULT_BASE_URL))
        # 百炼默认会改写提示词；镜匠的提示词里有颜色、款式等硬性要求，默认不让它改写。
        self.prompt_extend = str(settings.get("prompt_extend") or "").strip().lower() in {"1", "true", "yes", "on"}

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def check_target(self):
        return f"{self.base_url}/tasks/shotwright-connection-check", self._headers, self.api_key

    def build_payload(self, request: Request) -> dict:
        media: list[dict] = []
        if request.mode in ("first_frame", "first_last") and request.first_frame is not None:
            media.append({"type": "first_frame", "url": data_url(request.first_frame, limit=MAX_IMAGE)})
        for path in request.references:
            media.append({"type": "reference_image", "url": data_url(path, limit=MAX_IMAGE)})
        parameters: dict = {
            "resolution": self.resolution.upper(),
            "duration": request.seconds,
            "prompt_extend": self.prompt_extend,
            "watermark": self.watermark,
        }
        if self.model.startswith("wan3"):
            # 有首帧时按首帧比例；文生和参考图生按画布比例。
            parameters["ratio"] = (
                "adaptive" if request.first_frame is not None or request.aspect not in _RATIOS else request.aspect
            )
            parameters["audio"] = self.generate_audio
        body: dict = {"model": self.model, "input": {"prompt": request.prompt}, "parameters": parameters}
        if media:
            body["input"]["media"] = media
        return body

    def _submit(self, client, request: Request) -> str:
        return self._paid_task.submit(
            client,
            f"{self.base_url}/services/aigc/video-generation/video-synthesis",
            self.build_payload(request),
            headers={**self._headers, "X-DashScope-Async": "enable"},
            label=LABEL,
            secret=self.api_key,
            task_id_of=lambda body: (body.get("output") or {}).get("task_id"),
            console=self.CONSOLE,
        )

    def _finish(self, client, task_id: str, output: Path) -> Path:
        body = self._paid_task.poll(
            client,
            f"{self.base_url}/tasks/{task_id}",
            task_id=task_id,
            headers=self._headers,
            secret=self.api_key,
            settings=self._poll_settings(
                success=frozenset({"succeeded"}),
                active=frozenset({"pending", "running"}),
                failure=frozenset({"failed", "canceled", "cancelled", "unknown"}),
                status_of=lambda body: str((body.get("output") or {}).get("task_status") or ""),
                error_of=lambda body: (body.get("output") or {}).get("message") or (body.get("output") or {}).get("code"),
            ),
        )
        url = str((body.get("output") or {}).get("video_url") or "")
        return self._paid_task.download(client, url, output.with_suffix(".mp4"), label=LABEL, task_id=task_id)


registry.register(
    ProviderSpec(
        kind="video_gen",
        id="wan",
        name="通义万相（阿里云百炼）",
        name_en="Wan (Alibaba Cloud Model Studio)",
        fields=(
            ConfigField("api_key", "百炼 API Key", required=True, secret=True, help="留空时使用通义千问大模型的 Key"),
            ConfigField(
                "model",
                "模型",
                default=DEFAULT_MODEL,
                help="wan3.0-video（文生、首帧、参考图都支持）/ wan3.0-video-prime（更快）/ wan2.7-i2v / wan2.7-t2v",
            ),
            ConfigField(
                "base_url",
                "接口地址",
                default=DEFAULT_BASE_URL,
                help="新版百炼控制台给出的地址，例如 https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1",
            ),
            ConfigField("resolution", "分辨率", default="1080p", help="480p / 720p / 1080p（wan2.7 只有 720p、1080p）"),
            ConfigField("draft_resolution", "草稿分辨率", default="480p"),
            ConfigField("max_duration", "最长秒数", help="留空按模型上限（wan3.0 30 秒）"),
            ConfigField("generate_audio", "生成声音", default="false", help="镜匠自己配音，默认不要模型的声音"),
            ConfigField("prompt_extend", "让百炼改写提示词", default="false"),
            ConfigField("price", "单价（元/秒）", help="留空用参考价：wan3.0 1080p 1.2、720p 0.6、480p 0.3"),
            ConfigField("poll_interval", "轮询间隔（秒）", default="10"),
            ConfigField("run_timeout", "最长等待（秒）", default="1800"),
        ),
        capabilities=frozenset({"text_to_video", "first_frame", "reference_images"}),
        paid=True,
        docs_url="https://help.aliyun.com/zh/model-studio/video-generate-edit-model/",
        shared_key_from=(("llm", "qwen"),),
        max_duration=30.0,
        has_audio=False,
        max_ref_images=10,
        prompt_languages=("zh", "en"),
        pricing=pricing_for(CATALOG, DEFAULT_MODEL),
        billing=billing_for(CATALOG, DEFAULT_MODEL),
        resolutions_of=resolutions_for(CATALOG, DEFAULT_MODEL),
    ),
    WanVideo,
)
