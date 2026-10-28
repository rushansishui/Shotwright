"""其他 AI 视频服务（万相、可灵、MiniMax、fal）：请求格式、任务轮询、模型能力和费用估算。

都用 httpx.MockTransport 模拟服务端，不会发出真实请求、不会扣费。
"""

import json

import httpx
import pytest
from conftest import make_clip, make_image

from shotwright.providers import registry
from shotwright.providers.base import ProviderError
from shotwright.providers.video_gen.common import VideoModel


class Server:
    """记录收到的请求，按路径返回预设响应。"""

    def __init__(self, routes):
        self.routes = routes
        self.requests: list[httpx.Request] = []
        self.video = b"mp4-bytes"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for (method, fragment), reply in self.routes.items():
            if request.method == method and fragment in str(request.url):
                return reply(request) if callable(reply) else reply
        if request.method == "GET" and "cdn.example" in str(request.url):
            return httpx.Response(200, content=self.video)
        return httpx.Response(404, json={"message": "no route"})

    def bodies(self, method: str = "POST") -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.method == method and r.content]


def _provider(provider_id, settings, server):
    instance = registry.create("video_gen", provider_id, settings)
    instance._client = lambda: httpx.Client(transport=httpx.MockTransport(server))
    instance.poll_interval = 0
    return instance


def test_snap_duration_to_supported_lengths():
    ranged = VideoModel("m", frozenset({"text"}), min_duration=2, max_duration=15)
    assert ranged.snap(0.3) == 2 and ranged.snap(7.2) == 8 and ranged.snap(40) == 15
    fixed = VideoModel("m", frozenset({"text"}), durations=(6, 10), min_duration=6, max_duration=10)
    assert fixed.snap(3) == 6 and fixed.snap(7) == 10 and fixed.snap(12) == 10
    assert fixed.snap(7, maximum=6) == 6  # 配置里限制了最长 6 秒

