"""不花钱用上大模型：智谱的免费模型和本机的 Ollama。"""

from __future__ import annotations

import httpx

from shotwright.providers import registry
from shotwright.providers.llm import openai_compat


def test_free_providers_are_listed_and_marked(client):
    llms = {p["id"]: p for p in client.get("/api/v2/providers").json()["llm"]}
    zhipu = llms["zhipu"]
    assert zhipu["free"] is True and zhipu["paid"] is False
    assert any(f["key"] == "api_key" and f["required"] for f in zhipu["fields"])
    assert zhipu["setup"], "免费的服务也要告诉用户去哪里拿 Key"

    ollama = llms["ollama"]
    assert ollama["free"] is True and ollama["paid"] is False
    # 本机模型不要 Key。
    assert not any(f["key"] == "api_key" for f in ollama["fields"])
    # 收费的预设照旧标收费、不标免费。
    assert llms["deepseek"]["paid"] is True and llms["deepseek"]["free"] is False


def test_ollama_is_not_configured_until_a_model_is_chosen(settings):
    spec = next(s for s in registry.specs() if s.kind == "llm" and s.id == "ollama")
    # 没装 Ollama、没选模型时不能显示成「可用」。
    assert registry.is_configured(spec, settings) is False
    assert spec.missing_fields({"model": "qwen3:8b"}) == []


class _FakeClient:
    """替换 httpx.Client：记下访问的地址，按预设返回结果或抛错。"""

    def __init__(self, result, seen):
        self.result = result
        self.seen = seen

    def __call__(self, **kwargs):
        self.seen["kwargs"] = kwargs
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        self.seen["url"] = url
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_local_ollama_lists_models_without_proxy(monkeypatch):
    seen: dict = {}
    response = httpx.Response(200, json={"models": [{"name": "qwen3:8b"}, {"name": "glm-4.7-flash"}, {}]})
    monkeypatch.setattr(openai_compat.httpx, "Client", _FakeClient(response, seen))
    assert openai_compat.local_ollama() == ["qwen3:8b", "glm-4.7-flash"]
    assert seen["url"] == "http://127.0.0.1:11434/api/tags"
    # 只连本机：不能走 HTTPS_PROXY 这类代理设置。
    assert seen["kwargs"]["trust_env"] is False


def test_local_ollama_returns_none_when_not_running(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(openai_compat.httpx, "Client", _FakeClient(httpx.ConnectError("refused"), seen))
    assert openai_compat.local_ollama() is None


def test_local_llm_endpoint(client, monkeypatch):
    monkeypatch.setattr(openai_compat, "local_ollama", lambda: ["qwen3:8b"])
    assert client.get("/api/v2/local-llm").json() == {"running": True, "models": ["qwen3:8b"]}
    monkeypatch.setattr(openai_compat, "local_ollama", lambda: None)
    assert client.get("/api/v2/local-llm").json() == {"running": False, "models": []}
