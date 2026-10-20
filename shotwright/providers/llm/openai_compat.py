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


# 预设：同一个实现，换默认的 Base URL 和推荐模型。用户只需填写 API Key；
# 各家模型更新很快，推荐模型可以在 config.toml 里用 model 覆盖。
PRESETS: tuple[dict, ...] = (
    {
        "id": "doubao",
        "name": "豆包 Seed（火山方舟）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-1-pro-260915",
        "timeout": "300",
        "docs_url": "https://www.volcengine.com/docs/82379/1330626",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "timeout": "180",
        "docs_url": "https://api-docs.deepseek.com/zh",
    },
    {
        "id": "kimi",
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k3",
        "timeout": "300",
        "docs_url": "https://platform.moonshot.cn/docs",
    },
    {
        "id": "qwen",
        "name": "通义千问（阿里云百炼）",
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
            fields=(
                ConfigField("api_key", "API Key", required=True, secret=True),
                ConfigField("model", "模型", required=True, default=preset["model"]),
                ConfigField("base_url", "Base URL", default=preset["base_url"]),
                ConfigField("timeout", "超时（秒）", default=preset["timeout"]),
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
        fields=(
            ConfigField("api_key", "API Key", secret=True),
            ConfigField("base_url", "Base URL", default="https://api.openai.com/v1"),
            ConfigField("model", "模型", required=True, default="gpt-4o-mini"),
        ),
        docs_url="https://platform.openai.com/docs/api-reference/chat",
    ),
    OpenAICompatibleLLM,
)
