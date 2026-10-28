"""可灵 Kling（快手）：文生视频 / 首帧 / 首尾帧 / 多图参考生视频（收费）。

开放平台文档：https://kling.ai/document-api （国内：https://app.klingai.com/cn/dev/document-api）

* 鉴权：填 API Key 时直接放在 ``Authorization: Bearer`` 里（可灵新推出的单密钥方式，适用于所有模型）；
  否则用 Access Key、Secret Key 签一个 JWT（HS256，``iss`` = Access Key）。
* 新模型的查询结果改成了 ``status`` / ``outputs[]`` 结构，旧模型是 ``data.task_status`` /
  ``data.task_result``，两种都认。
* ``POST /v1/videos/text2video``、``/v1/videos/image2video``，查询
  ``GET /v1/videos/{同一路径}/{task_id}``。不同接口的任务要到各自路径查询，所以保存的任务
  id 带上接口名（``image2video:xxx``），重启后才知道去哪里查。
* 图片用不带 ``data:`` 前缀的 base64，最大 10MB。
* 参考图：默认把第一张参考图作为首帧（可灵首帧生视频对商品外观保持得很好）。填了
  ``reference_model``（例如 kling-v1-6）时，改用多图参考接口 ``/v1/videos/multi-image2video``，
  最多 4 张。
* 生成的视频链接保留 30 天，镜匠拿到后立即下载。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderSpec
from shotwright.providers.video_gen.common import (
    Request,
    TaskVideoProvider,
    VideoModel,
    billing_for,
    find_url,
    pricing_for,
    raw_base64,
    resolutions_for,
)

LABEL = "可灵"
DEFAULT_BASE_URL = "https://api-beijing.klingai.com"
DEFAULT_MODEL = "kling-v3"
_ASPECTS = ("16:9", "9:16", "1:1")
_MODES = {"720p": "std", "1080p": "pro", "4k": "4k"}

# 参考价（元 / 秒）：可灵按积分计费、各套餐单价不同，这里是按公开报价折算的估算值，
# 请在 config.toml 里用 price 填自己账户的实际单价。
CATALOG: dict[str, VideoModel] = {
    "kling-v3": VideoModel(
        "kling-v3",
        frozenset({"text", "first_frame", "first_last"}),
        min_duration=3,
        max_duration=15,
        resolutions=("720p", "1080p", "4k"),
        audio="optional",
        price={"720p": 0.6, "1080p": 0.9, "4k": 1.8},
        aspects=_ASPECTS,
    ),
    "kling-v2-6": VideoModel(
        "kling-v2-6",
        frozenset({"text", "first_frame", "first_last"}),
        durations=(5, 10),
        min_duration=5,
        max_duration=10,
        resolutions=("720p", "1080p"),
        audio="optional",
        price={"720p": 0.4, "1080p": 0.7},
        aspects=_ASPECTS,
    ),
    "kling-v2-1": VideoModel(
        "kling-v2-1",
        frozenset({"first_frame", "first_last"}),
        durations=(5, 10),
        min_duration=5,
        max_duration=10,
        resolutions=("720p", "1080p"),
        price={"720p": 0.4, "1080p": 0.7},
        aspects=_ASPECTS,
    ),
    "kling-v1-6": VideoModel(
        "kling-v1-6",
        frozenset({"text", "first_frame", "first_last", "references"}),
        durations=(5, 10),
        min_duration=5,
        max_duration=10,
        resolutions=("720p", "1080p"),
        max_references=4,
        price={"720p": 0.3, "1080p": 0.5},
        aspects=_ASPECTS,
    ),
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_token(access_key: str, secret_key: str, *, lifetime: int = 3600, now: float | None = None) -> str:
    """可灵要求的 JWT：HS256，``iss`` 是 Access Key，``nbf`` 提前 5 秒防止时钟误差。"""
    issued = int(now if now is not None else time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps({"iss": access_key, "exp": issued + lifetime, "nbf": issued - 5}, separators=(",", ":")).encode()
    )
    signing = f"{header}.{payload}".encode()
    signature = _b64url(hmac.new(secret_key.encode(), signing, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


class KlingVideo(TaskVideoProvider):
    LABEL = LABEL
    CATALOG = CATALOG
    DEFAULT_MODEL = DEFAULT_MODEL
    MAX_PROMPT = 2500
    CONSOLE = "可灵开放平台控制台"

    def __init__(self, settings: dict[str, str]):
        super().__init__(settings)
        self.api_key = str(settings.get("api_key") or "").strip()
        self.access_key = str(settings.get("access_key") or "").strip()
        self.secret_key = str(settings.get("secret_key") or "").strip()
        if not self.api_key and not (self.access_key and self.secret_key):
            raise ValueError("请填 API Key，或者同时填 Access Key 和 Secret Key")
        self.base_url = (settings.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.reference_model = str(settings.get("reference_model") or "").strip()
        if self.reference_model:
            self.reference_profile = CATALOG.get(self.reference_model, CATALOG["kling-v1-6"])

    @property
    def max_reference_images(self) -> int:
        # 默认不走多图参考：把第一张参考图作为首帧，用主模型生成。
        return self.reference_profile.max_references if self.reference_model else 0

    def plan(self, prompt, *, aspect, duration, first_frame, references):
        refs = list(references or [])[: self.max_reference_images] if first_frame is None else []
        if refs and self.reference_model:
            seconds = self.reference_profile.snap(duration)
            return Request(self._prompt(prompt), "references", seconds, aspect, None, refs)
        return super().plan(prompt, aspect=aspect, duration=duration, first_frame=first_frame, references=references)

    @property
    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        token = make_token(self.access_key, self.secret_key, lifetime=int(self.run_timeout) + 600)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    @property
    def _secret(self) -> str:
        return self.api_key or self.secret_key

    def check_target(self):
        return f"{self.base_url}/v1/videos/text2video/shotwright-check", self._headers, self._secret

    def build_payload(self, request: Request) -> tuple[str, dict]:
        """返回 (接口名, 请求体)。"""
        common: dict = {"prompt": request.prompt, "mode": _MODES.get(self.resolution, "std")}
        if request.mode == "references":
            body = {
                **common,
                "model_name": self.reference_model,
                "image_list": [{"image": raw_base64(path)} for path in request.references],
                "duration": str(request.seconds),
                "aspect_ratio": request.aspect if request.aspect in _ASPECTS else "9:16",
            }
            return "multi-image2video", body
        body = {**common, "model_name": self.model, "duration": str(request.seconds)}
        if self.profile.audio == "optional":
            body["sound"] = "on" if self.generate_audio else "off"
        if request.first_frame is not None:
            body["image"] = raw_base64(request.first_frame)
            return "image2video", body
        body["aspect_ratio"] = request.aspect if request.aspect in _ASPECTS else "9:16"
        return "text2video", body

    def _submit(self, client, request: Request) -> str:
        endpoint, body = self.build_payload(request)
        task_id = self._paid_task.submit(
            client,
            f"{self.base_url}/v1/videos/{endpoint}",
            body,
            headers=self._headers,
            label=LABEL,
            secret=self._secret,
            task_id_of=lambda reply: (reply.get("data") or {}).get("task_id") or reply.get("task_id") or reply.get("id"),
            reject_of=lambda reply: reply.get("message") if reply.get("code") not in (None, 0, "0") else None,
            console=self.CONSOLE,
        )
        return f"{endpoint}:{task_id}"

    def _finish(self, client, task_id: str, output: Path) -> Path:
        endpoint, _, remote_id = task_id.partition(":")
        if not remote_id:
            endpoint, remote_id = "image2video", task_id
        body = self._paid_task.poll(
            client,
            f"{self.base_url}/v1/videos/{endpoint}/{remote_id}",
            task_id=remote_id,
            headers=self._headers,
            secret=self._secret,
            settings=self._poll_settings(
                success=frozenset({"succeed", "succeeded", "success"}),
                active=frozenset({"submitted", "processing", "queued", "running"}),
                failure=frozenset({"failed", "fail"}),
                status_of=lambda reply: str((reply.get("data") or {}).get("task_status") or reply.get("status") or ""),
                error_of=lambda reply: (reply.get("data") or {}).get("task_status_msg")
                or reply.get("error")
                or reply.get("message"),
            ),
        )
        url = find_url((body.get("data") or {}).get("task_result") or body.get("outputs") or body)
        return self._paid_task.download(client, url, output.with_suffix(".mp4"), label=LABEL, task_id=remote_id)


registry.register(
    ProviderSpec(
        kind="video_gen",
        id="kling",
        name="可灵 Kling",
        name_en="Kling",
        fields=(
            ConfigField("api_key", "API Key", secret=True, help="可灵新的单密钥鉴权；填了就不用填下面两项"),
            ConfigField("access_key", "Access Key", secret=True, help="没有 API Key 时与 Secret Key 一起用"),
            ConfigField("secret_key", "Secret Key", secret=True),
            ConfigField("model", "模型", default=DEFAULT_MODEL, help="kling-v3 / kling-v2-6 / kling-v2-1 / kling-v1-6"),
            ConfigField(
                "base_url",
                "接口地址",
                default=DEFAULT_BASE_URL,
                help="国内 https://api-beijing.klingai.com，国际 https://api-singapore.klingai.com",
            ),
            ConfigField("resolution", "分辨率", default="1080p", help="720p（标准）/ 1080p（高品质）/ 4k（仅 kling-v3）"),
            ConfigField("draft_resolution", "草稿分辨率", default="720p"),
            ConfigField(
                "reference_model",
                "多图参考模型",
                help="留空时把第一张参考图作为首帧；填 kling-v1-6 时用多图参考接口（最多 4 张）",
            ),
            ConfigField("max_duration", "最长秒数", help="留空按模型上限（kling-v3 15 秒）"),
            ConfigField("generate_audio", "生成声音", default="false"),
            ConfigField("price", "单价（元/秒）", help="可灵按积分计费，请填自己账户的实际单价；留空用估算值"),
            ConfigField("poll_interval", "轮询间隔（秒）", default="10"),
            ConfigField("run_timeout", "最长等待（秒）", default="1800"),
        ),
        capabilities=frozenset({"text_to_video", "first_frame"}),
        required_any=(("api_key",), ("access_key", "secret_key")),
        paid=True,
        docs_url="https://kling.ai/document-api",
        max_duration=15.0,
        has_audio=False,
        prompt_languages=("zh", "en"),
        pricing=pricing_for(CATALOG, DEFAULT_MODEL),
        billing=billing_for(CATALOG, DEFAULT_MODEL),
        resolutions_of=resolutions_for(CATALOG, DEFAULT_MODEL),
    ),
    KlingVideo,
)
