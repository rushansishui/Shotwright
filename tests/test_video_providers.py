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


# ---- 通义万相 -----------------------------------------------------------------


def _wan_server(status="SUCCEEDED"):
    return Server(
        {
            ("POST", "/video-synthesis"): httpx.Response(200, json={"output": {"task_id": "wt1", "task_status": "PENDING"}}),
            ("GET", "/tasks/wt1"): httpx.Response(
                200,
                json={"output": {"task_status": status, "video_url": "https://cdn.example/w.mp4", "message": "内容违规"}},
            ),
        }
    )


def test_wan_text_to_video_payload_and_result(settings, tmp_path):
    settings.providers["video_gen"] = {"wan": {"api_key": "k"}}
    server = _wan_server()
    wan = _provider("wan", settings, server)
    tasks = []
    path = wan.generate("P1 挂在衣架上", aspect="9:16", duration=7.3, output=tmp_path / "v", on_task=tasks.append)
    assert path.read_bytes() == b"mp4-bytes" and tasks == ["wt1"]
    submit = server.requests[0]
    assert submit.headers["X-DashScope-Async"] == "enable" and submit.headers["Authorization"] == "Bearer k"
    body = server.bodies()[0]
    assert body["model"] == "wan3.0-video" and "media" not in body["input"]
    assert body["parameters"] == {
        "resolution": "1080P", "duration": 8, "prompt_extend": False, "watermark": False, "ratio": "9:16", "audio": False
    }


def test_wan_reference_images_and_first_frame(settings, tmp_path):
    settings.providers["video_gen"] = {"wan": {"api_key": "k", "resolution": "720p"}}
    refs = [make_image(tmp_path / "a.png"), make_image(tmp_path / "b.png", size="320x320")]
    server = _wan_server()
    wan = _provider("wan", settings, server)
    assert wan.max_reference_images == 10
    wan.generate("x", aspect="9:16", duration=5, output=tmp_path / "v", reference_images=refs)
    media = server.bodies()[0]["input"]["media"]
    assert [m["type"] for m in media] == ["reference_image", "reference_image"]
    assert media[0]["url"].startswith("data:image/png;base64,")
    wan.generate("x", aspect="9:16", duration=5, output=tmp_path / "w", first_frame=refs[0])
    second = server.bodies()[1]
    assert [m["type"] for m in second["input"]["media"]] == ["first_frame"]
    assert second["parameters"]["ratio"] == "adaptive" and second["parameters"]["resolution"] == "720P"


def test_wan_failed_task_reports_reason(settings, tmp_path):
    settings.providers["video_gen"] = {"wan": {"api_key": "k"}}
    wan = _provider("wan", settings, _wan_server(status="FAILED"))
    with pytest.raises(ProviderError, match="内容违规"):
        wan.generate("x", aspect="9:16", duration=5, output=tmp_path / "v")


def test_wan_image_only_model_needs_a_first_frame(settings, tmp_path):
    settings.providers["video_gen"] = {"wan": {"api_key": "k", "model": "wan2.7-i2v-2026-04-25"}}
    wan = _provider("wan", settings, _wan_server())
    assert wan.profile.id == "wan2.7-i2v" and wan.max_reference_images == 0
    with pytest.raises(ProviderError, match="需要先选首帧"):
        wan.generate("x", aspect="9:16", duration=5, output=tmp_path / "v")
    with pytest.raises(ProviderError, match="配置有误"):
        registry.create("video_gen", "wan", settings.__class__(
            storage_dir=tmp_path, providers={"video_gen": {"wan": {"api_key": "k", "model": "wan2.7-i2v", "resolution": "480p"}}}
        ))


def test_wan_shares_the_qwen_key(settings):
    settings.providers["llm"] = {"qwen": {"api_key": "qwen-key"}}
    settings.providers["video_gen"] = {"wan": {}}
    assert registry.create("video_gen", "wan", settings).api_key == "qwen-key"


def test_wan_base_url_is_normalized_and_bare_404_names_the_url(tmp_path):
    """base_url 少写 /api/v1 或贴了完整地址都能用；没有说明的 404 提示检查地址，而不是去开通模型。"""
    from shotwright.providers.video_gen.wan import WanVideo, api_base

    host = "https://ws1.cn-beijing.maas.aliyuncs.com"
    assert api_base(host) == f"{host}/api/v1"
    assert api_base(f"{host}/api/v1/") == f"{host}/api/v1"
    assert api_base(f"{host}/api/v1/services/aigc/video-generation/video-synthesis") == f"{host}/api/v1"
    assert api_base("https://dashscope.aliyuncs.com/compatible-mode/v1") == "https://dashscope.aliyuncs.com/api/v1"
    with pytest.raises(ValueError, match="业务空间"):
        api_base("https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1")

    provider = WanVideo({"api_key": "k", "base_url": host, "poll_interval": "0"})
    provider._client = lambda: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))  # type: ignore[method-assign]
    with pytest.raises(ProviderError) as info:
        provider.generate("x", aspect="9:16", duration=5, output=tmp_path / "o")
    message = str(info.value)
    assert "接口地址不存在" in message and f"{host}/api/v1/services/aigc/video-generation/video-synthesis" in message
    assert "开通" not in message