"""文生图：OpenAI 兼容的 ``/images/generations`` 接口（收费）。

* ``seedream``：火山方舟 Seedream，与豆包大模型共用方舟 API Key；
* ``openai``：其他兼容 OpenAI Images 接口的服务，自行填写 Base URL 和模型。

生成的图片在渲染时配合慢推镜头（Ken Burns），作为成本较低的 AI 画面。
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

import httpx

from shotwright.providers import paid_task, registry
from shotwright.providers.base import ConfigField, ProviderError, ProviderSpec, UnconfirmedTaskError

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
MAX_IMAGE = 50 * 1024 * 1024
SEEDREAM_SIZES = {"9:16": "1440x2560", "16:9": "2560x1440", "1:1": "2048x2048"}
OPENAI_SIZES = {"9:16": "1024x1792", "16:9": "1792x1024", "1:1": "1024x1024"}


class OpenAIImages:
    def __init__(self, settings: dict[str, str], *, label: str, sizes: dict[str, str]):
        self.label = label
        self.api_key = settings["api_key"]
        self.base_url = settings["base_url"].rstrip("/").removesuffix("/images/generations")
        self.model = settings["model"]
        self.sizes = sizes
        self.size_override = settings.get("size") or ""
        self.watermark = str(settings.get("watermark") or "").lower() in {"1", "true", "yes", "on"}
        self.extra_watermark = label == "Seedream"
        self.proxy = settings.get("proxy") or None
        self.timeout = float(settings.get("timeout") or 180)

    def build_payload(self, prompt: str, aspect: str, references: list[Path] | None = None) -> dict:
        payload = {
            "model": self.model,
            "prompt": prompt.strip(),
            "size": self.size_override or self.sizes.get(aspect, self.sizes["9:16"]),
            "response_format": "url",
        }
        if references:
            # 方舟 Seedream 4.0 起支持参考图（图生图 / 多图参考）：Base64 data URI，一张传字符串、多张传数组。
            images = [_data_uri(path) for path in references[:MAX_REFERENCES]]
            payload["image"] = images[0] if len(images) == 1 else images
        if self.extra_watermark:
            # 方舟 Seedream 默认不开组图，一次只生成一张；5.0 pro / flash 不支持组图参数，所以不传。
            payload["watermark"] = self.watermark
        return payload

    def generate(self, prompt: str, *, aspect: str, output: Path, references: list[Path] | None = None) -> Path:
        """``references`` 是主体参考图（产品实拍、人物照片），只有 Seedream 会带上。"""
        paid_task.log.info("%s 提交生图请求：模型 %s", self.label, self.model)
        refs = list(references or []) if self.extra_watermark else []
        try:
            try:
                path = self._generate(prompt, aspect=aspect, output=output, references=refs)
            except ProviderError as exc:
                if not refs or isinstance(exc, UnconfirmedTaskError):
                    raise
                # 请求被拒绝（4xx）不扣费：多半是模型不支持参考图（例如 3.0 文生图），去掉参考图再试一次。
                paid_task.log.warning("%s 不接受参考图，改为只用文字生成：%s", self.label, exc)
                path = self._generate(prompt, aspect=aspect, output=output)
        except UnconfirmedTaskError as exc:
            paid_task.log.error("%s 生图结果无法确认，可能已扣费：%s", self.label, exc)
            raise
        except ProviderError as exc:
            paid_task.log.warning("%s 生图失败：%s", self.label, exc)
            raise
        paid_task.log.info("%s 生图完成：%s", self.label, path.name)
        return path

    def _generate(self, prompt: str, *, aspect: str, output: Path, references: list[Path] | None = None) -> Path:
        if not prompt.strip():
            raise ProviderError("缺少画面提示词")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/images/generations"
        with httpx.Client(timeout=self.timeout, proxy=self.proxy) as client:
            # 同步接口：一次请求就是一次扣费，同样不自动重试。
            try:
                response = client.post(url, json=self.build_payload(prompt, aspect, references), headers=headers)
            except httpx.HTTPError as exc:
                raise UnconfirmedTaskError(
                    f"{self.label} 请求没有收到响应，可能已经扣费，请到服务商控制台确认（{type(exc).__name__}）"
                ) from exc
            status = response.status_code
            if status >= 500 or 300 <= status < 400:
                raise UnconfirmedTaskError(f"{self.label} 返回 HTTP {status}，可能已经扣费")
            if status >= 400:
                raise ProviderError(
                    paid_task.rejection_message(self.label, response, self.api_key, self.model)
                )
            try:
                items = response.json().get("data") or []
                item = items[0]
            except (ValueError, AttributeError, IndexError, KeyError) as exc:
                raise UnconfirmedTaskError(f"{self.label} 的响应无法解析，可能已经扣费") from exc
            output = output.with_suffix(".png")
            if item.get("b64_json"):
                try:
                    data = base64.b64decode(item["b64_json"], validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ProviderError(f"{self.label} 返回的图片数据损坏") from exc
                if not data or len(data) > MAX_IMAGE:
                    raise ProviderError(f"{self.label} 返回的图片大小异常")
                if paid_task.sniff_image_type(data) == "image/jpeg":
                    output = output.with_suffix(".jpg")
                output.write_bytes(data)
                return output
            url = str(item.get("url") or "")
            suffix = ".jpg" if url.split("?")[0].lower().endswith((".jpg", ".jpeg")) else ".png"
            return paid_task.download(client, url, output.with_suffix(suffix), label=self.label)


MAX_REFERENCES = 4
"""一次最多带几张参考图。"""
MAX_REFERENCE_BYTES = 8 * 1024 * 1024


def _data_uri(path: Path) -> str:
    data = path.read_bytes()
    if len(data) > MAX_REFERENCE_BYTES:
        raise ProviderError(f"参考图 {path.name} 超过 8MB，请换一张小一点的图片")
    kind = paid_task.sniff_image_type(data) or "image/png"
    return f"data:{kind};base64,{base64.b64encode(data).decode()}"


def _seedream(settings: dict[str, str]) -> OpenAIImages:
    return OpenAIImages(settings, label="Seedream", sizes=SEEDREAM_SIZES)


def _openai(settings: dict[str, str]) -> OpenAIImages:
    return OpenAIImages(settings, label="文生图", sizes=OPENAI_SIZES)


registry.register(
    ProviderSpec(
        kind="image_gen",
        id="seedream",
        name="Seedream（火山方舟）",
        name_en="Seedream (Volcengine Ark)",
        fields=(
            ConfigField(
                "api_key", "方舟 API Key", required=True, secret=True, help="留空时使用豆包大模型的 Key"
            ),
            ConfigField("model", "模型", default="doubao-seedream-5-0-pro-260628"),
            ConfigField("base_url", "Base URL", default=ARK_BASE_URL),
            ConfigField("size", "尺寸", help="留空时按画布比例自动选择，例如 1440x2560"),
            ConfigField("watermark", "加水印", default="false"),
        ),
        capabilities=frozenset({"text_to_image"}),
        paid=True,
        docs_url="https://www.volcengine.com/docs/82379/1541523",
        shared_key_from=(("llm", "doubao"),),
        prompt_languages=("zh", "en"),
    ),
    _seedream,
)

registry.register(
    ProviderSpec(
        kind="image_gen",
        id="openai",
        name="OpenAI 兼容文生图",
        name_en="OpenAI-compatible images",
        fields=(
            ConfigField("api_key", "API Key", required=True, secret=True),
            ConfigField("base_url", "Base URL", required=True, default="https://api.openai.com/v1"),
            ConfigField("model", "模型", required=True),
            ConfigField("size", "尺寸", help="留空时按画布比例自动选择"),
        ),
        capabilities=frozenset({"text_to_image"}),
        paid=True,
        # 主流的 OpenAI 兼容文生图模型都能直接理解中文。
        prompt_languages=("en", "zh"),
    ),
    _openai,
)
