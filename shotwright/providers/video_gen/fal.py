"""fal.ai 聚合平台：填模型名就能用上面的各家视频模型（Veo、Kling、Vidu、Luma……，收费）。

队列接口文档：https://fal.ai/docs/documentation/model-apis/inference/queue.md

* 提交 ``POST https://queue.fal.run/{模型路径}``，请求头 ``Authorization: Key {FAL_KEY}``，
  请求体就是这个模型的输入参数。
* 查询 ``GET https://queue.fal.run/{owner}/{app}/requests/{request_id}/status``（注意只用模型路径的
  前两段），状态依次是 IN_QUEUE、IN_PROGRESS、COMPLETED；失败也是 COMPLETED，但带 ``error``。
* 取结果 ``GET https://queue.fal.run/{owner}/{app}/requests/{request_id}``，视频地址在
  ``video.url``（各模型略有不同，宽松地找第一个 url）。

每个模型的参数名不完全一样（时长有的要数字、有的要 "5"、有的要 "8s"），所以这些都可以在
config.toml 里改；``extra`` 填一段 JSON，原样合并进请求体（例如分辨率、是否生成声音）。
图片用 ``data:`` 开头的 base64 传。国内访问需要在 ``proxy`` 里填代理。
"""

from __future__ import annotations

import json
from pathlib import Path

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderError, ProviderSpec
from shotwright.providers.video_gen.common import (
    Request,
    TaskVideoProvider,
    VideoModel,
    config_price,
    data_url,
    find_url,
    generic_model,
)

LABEL = "fal.ai"
QUEUE_URL = "https://queue.fal.run"
_DURATION_FORMATS = ("int", "string", "seconds", "none")


def _app(model: str) -> str:
    """状态和结果接口只用模型路径的前两段：fal-ai/kling-video/v2.1/... → fal-ai/kling-video。"""
    parts = [part for part in model.strip("/").split("/") if part]
    return "/".join(parts[:2])


def _profile(model: str, settings: dict[str, str]) -> VideoModel:
    image_only = "image-to-video" in model and not settings.get("text_model")
    modes = {"first_frame"} if image_only else {"text", "first_frame"}
    base = VideoModel(model, frozenset(modes), min_duration=5, max_duration=10, resolutions=("720p",))
    return generic_model(model, settings, base=base)


class FalVideo(TaskVideoProvider):
    LABEL = LABEL
    DEFAULT_MODEL = ""
    MAX_PROMPT = 4000
    CONSOLE = "fal.ai 控制台"

    def __init__(self, settings: dict[str, str]):
        settings = {**settings, "resolution": "720p"}  # 分辨率由 extra 决定，这里只为通过校验
        self.CATALOG = {str(settings.get("model") or ""): _profile(str(settings.get("model") or ""), settings)}
        super().__init__(settings)
        self.api_key = settings["api_key"]
        self.image_model = str(settings.get("image_model") or "").strip()
        if self.image_model:
            self.profile = VideoModel(
                self.profile.id,
                self.profile.modes | {"text", "first_frame"},
                min_duration=self.profile.min_duration,
                max_duration=self.profile.max_duration,
                durations=self.profile.durations,
                resolutions=self.profile.resolutions,
                max_references=self.profile.max_references,
            )
        self.duration_format = str(settings.get("duration_format") or "string").strip().lower()
        if self.duration_format not in _DURATION_FORMATS:
            raise ValueError(f"duration_format 只能是 {' / '.join(_DURATION_FORMATS)}")
        self.image_field = str(settings.get("image_field") or "image_url").strip()
        # 有的模型不认识 aspect_ratio（会报错），填 none 不传。
        aspect_field = str(settings.get("aspect_field") or "aspect_ratio").strip()
        self.aspect_field = "" if aspect_field.lower() == "none" else aspect_field
        self.references_field = str(settings.get("references_field") or "").strip()
        if self.profile.max_references and not self.references_field:
            raise ValueError("填了 max_references 时还要填 references_field（参考图列表的参数名）")
        raw_extra = str(settings.get("extra") or "").strip()
        try:
            self.extra = json.loads(raw_extra) if raw_extra else {}
        except ValueError as exc:
            raise ValueError(f"extra 不是合法的 JSON：{exc}") from exc
        if not isinstance(self.extra, dict):
            raise ValueError("extra 必须是 JSON 对象，例如 {\"resolution\": \"1080p\"}")
        self.queue_url = (settings.get("queue_url") or QUEUE_URL).rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Key {self.api_key}", "Content-Type": "application/json"}

    def _duration(self, seconds: int):
        if self.duration_format == "int":
            return seconds
        if self.duration_format == "seconds":
            return f"{seconds}s"
        return str(seconds)

    def build_payload(self, request: Request) -> tuple[str, dict]:
        """返回 (模型路径, 请求体)。"""
        body: dict = {"prompt": request.prompt}
        if self.duration_format != "none":
            body["duration"] = self._duration(request.seconds)
        model = self.model
        if request.first_frame is not None:
            body[self.image_field] = data_url(request.first_frame)
            model = self.image_model or self.model
        elif self.aspect_field:
            body[self.aspect_field] = request.aspect
        if request.references:
            body[self.references_field] = [data_url(path) for path in request.references]
        body.update(self.extra)
        return model, body

    def _submit(self, client, request: Request) -> str:
        model, body = self.build_payload(request)
        return f"{_app(model)}|{self._submit_raw(client, model, body)}"

    def _submit_raw(self, client, model: str, body: dict) -> str:
        # fal 的请求体就是模型参数，不能夹带 model 字段，所以单独记日志里的模型名。
        self._paid_task.log.info("%s 使用模型 %s", LABEL, model)
        return self._paid_task.submit(
            client,
            f"{self.queue_url}/{model.strip('/')}",
            body,
            headers=self._headers,
            label=LABEL,
            secret=self.api_key,
            task_id_field="request_id",
            console=self.CONSOLE,
        )

    def _finish(self, client, task_id: str, output: Path) -> Path:
        app, _, request_id = task_id.partition("|")
        if not request_id:
            app, request_id = _app(self.model), task_id
        base = f"{self.queue_url}/{app}/requests/{request_id}"
        status = self._paid_task.poll(
            client,
            f"{base}/status",
            task_id=request_id,
            headers=self._headers,
            secret=self.api_key,
            settings=self._poll_settings(
                success=frozenset({"completed"}),
                active=frozenset({"in_queue", "in_progress"}),
                failure=frozenset({"failed", "error"}),
            ),
        )
        if status.get("error"):
            raise ProviderError(f"{LABEL} 任务失败：{str(status['error'])[:300]}，任务 id：{request_id}")
        response = client.get(base, headers=self._headers)
        if response.status_code >= 300:
            raise ProviderError(f"{LABEL} 取结果失败：HTTP {response.status_code}，任务 id：{request_id}")
        url = find_url(response.json(), ("url",))
        return self._paid_task.download(client, url, output.with_suffix(".mp4"), label=LABEL, task_id=request_id)


def _price(settings: dict[str, str], _resolution: str) -> float:
    return config_price(settings) or 0.0


def _billing(settings: dict[str, str], seconds: float) -> float:
    model = str(settings.get("model") or "")
    return float(_profile(model, settings).snap(seconds))


registry.register(
    ProviderSpec(
        kind="video_gen",
        id="fal",
        name="fal.ai（聚合平台）",
        name_en="fal.ai (aggregator)",
        fields=(
            ConfigField("api_key", "FAL Key", required=True, secret=True),
            ConfigField(
                "model",
                "模型路径",
                required=True,
                help="fal 模型页上的路径，例如 fal-ai/veo3/fast 或 fal-ai/kling-video/v2.1/standard/text-to-video",
            ),
            ConfigField("image_model", "图生视频模型路径", help="有首帧时改用这个模型（可选）"),
            ConfigField("image_field", "首帧参数名", default="image_url", help="可灵等模型是 start_image_url"),
            ConfigField("aspect_field", "画面比例参数名", default="aspect_ratio", help="模型没有这个参数时填 none"),
            ConfigField("duration_format", "时长写法", default="string", help="int（5）/ string（\"5\"）/ seconds（\"5s\"）/ none（不传）"),
            ConfigField("durations", "可选时长", help="逗号分隔，例如 5,10；留空表示 5～10 秒的整秒"),
            ConfigField("min_duration", "最短秒数"),
            ConfigField("max_duration", "最长秒数"),
            ConfigField("max_references", "参考图张数", help="模型支持参考图时填上限，同时填参考图参数名"),
            ConfigField("references_field", "参考图参数名", help="例如 reference_image_urls"),
            ConfigField("extra", "其他参数（JSON）", help='原样合并进请求，例如 {"resolution": "1080p", "generate_audio": false}'),
            ConfigField("price", "单价（元/秒）", help="填上才能做费用估算和预算控制"),
            ConfigField("poll_interval", "轮询间隔（秒）", default="10"),
            ConfigField("run_timeout", "最长等待（秒）", default="1800"),
        ),
        capabilities=frozenset({"text_to_video", "first_frame"}),
        paid=True,
        docs_url="https://fal.ai/explore/text-to-video-apis",
        max_duration=10.0,
        has_audio=False,
        prompt_languages=("en",),
        pricing=_price,
        billing=_billing,
    ),
    FalVideo,
)