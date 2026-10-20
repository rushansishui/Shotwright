"""OpenAI 兼容接口的大模型。

绝大多数大模型服务（OpenAI、DeepSeek、Moonshot、通义千问、智谱、硅基流动、
本地的 Ollama / LM Studio 等）都提供 ``/chat/completions`` 兼容接口，
只需改 ``base_url`` 和 ``model`` 即可接入。
"""

from __future__ import annotations

import httpx

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderError, ProviderSpec


class OpenAICompatibleLLM:
    def __init__(self, settings: dict[str, str]):
        self.api_key = settings.get("api_key", "")
        base_url = settings.get("base_url", "https://api.openai.com/v1").strip().rstrip("/")
        # 常见误填：把完整的接口地址当成 Base URL。
        if base_url.endswith("/chat/completions"):
            base_url = base_url[: -len("/chat/completions")]
        self.base_url = base_url
        self.model = settings.get("model", "gpt-4o-mini")
        self.timeout = float(settings.get("timeout", "120"))
        self.proxy = settings.get("proxy") or None

    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        payload: dict = {"model": self.model, "messages": messages, "temperature": 0.7}
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


registry.register(
    ProviderSpec(
        kind="llm",
        id="openai",
        name="OpenAI 兼容接口",
        fields=(
            ConfigField("api_key", "API Key", secret=True),
            ConfigField("base_url", "Base URL", default="https://api.openai.com/v1"),
            ConfigField("model", "模型", required=True, default="gpt-4o-mini"),
        ),
        docs_url="https://platform.openai.com/docs/api-reference/chat",
    ),
    OpenAICompatibleLLM,
)
