"""OpenAI 兼容接口的大模型。

绝大多数大模型服务（OpenAI、DeepSeek、Moonshot、通义千问、智谱、硅基流动、
本地的 Ollama / LM Studio 等）都提供 ``/chat/completions`` 兼容接口，
只需改 ``base_url`` 和 ``model`` 即可接入。
"""

from __future__ import annotations

import base64
from pathlib import Path

import httpx

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderError, ProviderNotConfigured, ProviderSpec

VISION_HELP = "用于检查生成的画面（产品是否走样、是否切题），需要支持看图的模型；留空时不检查"


class OpenAICompatibleLLM:
    def __init__(self, settings: dict[str, str]):
        self.api_key = settings.get("api_key", "")
        base_url = settings.get("base_url", "https://api.openai.com/v1").strip().rstrip("/")
        # 常见误填：把完整的接口地址当成 Base URL。
        if base_url.endswith("/chat/completions"):
            base_url = base_url[: -len("/chat/completions")]
        self.base_url = base_url
        self.model = settings.get("model", "gpt-4o-mini")
        self.vision_model = (settings.get("vision_model") or "").strip()
        """看图模型：用于检查生成的画面（主体是否走样、是否切题）。为空时不做看图检查。"""
        self.timeout = float(settings.get("timeout", "120"))
        self.proxy = settings.get("proxy") or None

    def check(self) -> tuple[bool, str]:
        """测试连接：用配置的模型回一个字（max_tokens=1，几乎不花钱），能同时验证 Key、地址和模型名。"""
        payload = {"model": self.model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.Client(timeout=min(self.timeout, 30), proxy=self.proxy) as client:
                response = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            message = str(exc).replace(self.api_key, "***") if self.api_key else str(exc)
            return False, f"连不上：{message[:200]}。请检查网络、代理或 base_url"
        if response.status_code < 300:
            return True, f"连接正常，模型 {self.model} 可用"
        from shotwright.providers.video_gen.common import describe_check

        ok, message = describe_check(response)
        if ok:  # 400 之类：Key 对，但模型名或参数不对
            detail = response.text[:200].replace(self.api_key, "***") if self.api_key else response.text[:200]
            return False, f"Key 有效，但请求被拒绝（HTTP {response.status_code}）：{detail}。请检查模型名"
        return False, message

    def see(self, prompt: str, images: list[Path]) -> str:
        """把几张图片和一段文字交给看图模型，返回它的回答（要求 JSON 时由调用方解析）。"""
        if not self.vision_model:
            raise ProviderNotConfigured("没有配置看图模型（vision_model）")
        content: list[dict] = [{"type": "text", "text": prompt}]
        for path in images:
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}})
        return self._chat(
            [{"role": "user", "content": content}], json_mode=True, model=self.vision_model, temperature=0.1
        )

    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        return self._chat(messages, json_mode=json_mode)

    def _chat(self, messages: list, *, json_mode: bool, model: str = "", temperature: float = 0.7) -> str:
        payload: dict = {"model": model or self.model, "messages": messages, "temperature": temperature}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.Client(timeout=self.timeout, proxy=self.proxy) as client:
                response = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                if response.status_code == 400 and json_mode:
                    # 部分兼容服务不支持 response_format，去掉后重试一次。
                    payload.pop("response_format", None)
                    response = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"大模型请求失败：{type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"大模型返回错误：HTTP {response.status_code}")
        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("大模型返回的格式无法解析") from exc


# 预设：同一个实现，换默认的 Base URL 和推荐模型。用户只需填写 API Key；
# 各家模型更新很快，推荐模型可以在 config.toml 里用 model 覆盖。
PRESETS: tuple[dict, ...] = (
    {
        "id": "doubao",
        "name": "豆包 Seed（火山方舟）",
        "name_en": "Doubao Seed (Volcengine Ark)",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-1-pro-260915",
        "timeout": "300",
        "docs_url": "https://www.volcengine.com/docs/82379/1330626",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "name_en": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "timeout": "180",
        "docs_url": "https://api-docs.deepseek.com/zh",
    },
    {
        "id": "kimi",
        "name": "Kimi（月之暗面）",
        "name_en": "Kimi (Moonshot AI)",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k3",
        "timeout": "300",
        "docs_url": "https://platform.moonshot.cn/docs",
    },
    {
        "id": "qwen",
        "name": "通义千问（阿里云百炼）",
        "name_en": "Qwen (Alibaba Cloud Model Studio)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "timeout": "180",
        "docs_url": "https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope",
    },
)

for preset in PRESETS:
    registry.register(
        ProviderSpec(
            kind="llm",
            id=preset["id"],
            name=preset["name"],
            name_en=preset["name_en"],
            fields=(
                ConfigField("api_key", "API Key", required=True, secret=True),
                ConfigField("model", "模型", required=True, default=preset["model"]),
                ConfigField("base_url", "Base URL", default=preset["base_url"]),
                ConfigField("timeout", "超时（秒）", default=preset["timeout"]),
                ConfigField("vision_model", "看图模型", help=VISION_HELP),
            ),
            paid=True,
            docs_url=preset["docs_url"],
        ),
        OpenAICompatibleLLM,
    )

registry.register(
    ProviderSpec(
        kind="llm",
        id="openai",
        name="OpenAI 兼容接口（自定义）",
        name_en="OpenAI-compatible API (custom)",
        fields=(
            ConfigField("api_key", "API Key", secret=True),
            ConfigField("base_url", "Base URL", default="https://api.openai.com/v1"),
            ConfigField("model", "模型", required=True, default="gpt-4o-mini"),
            ConfigField("vision_model", "看图模型", help=VISION_HELP),
        ),
        docs_url="https://platform.openai.com/docs/api-reference/chat",
    ),
    OpenAICompatibleLLM,
)
