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


# ---- 可灵 ---------------------------------------------------------------------


def _kling_server(reject=False):
    def submit(request):
        if reject and b"image_list" in request.content:
            return httpx.Response(400, json={"code": 1201, "message": "model not supported"})
        return httpx.Response(200, json={"code": 0, "message": "SUCCEED", "data": {"task_id": "kt1", "task_status": "submitted"}})

    done = {"code": 0, "data": {"task_status": "succeed", "task_result": {"videos": [{"url": "https://cdn.example/k.mp4"}]}}}
    return Server(
        {
            ("POST", "/v1/videos/"): submit,
            ("GET", "/kt1"): httpx.Response(200, json=done),
        }
    )


def test_kling_token_is_a_valid_hs256_jwt():
    import base64
    import hashlib
    import hmac

    from shotwright.providers.video_gen.kling import make_token

    token = make_token("ak", "sk", lifetime=1800, now=1_000_000)
    header, payload, signature = token.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=="))
    assert decoded == {"iss": "ak", "exp": 1_001_800, "nbf": 999_995}
    expected = base64.urlsafe_b64encode(hmac.new(b"sk", f"{header}.{payload}".encode(), hashlib.sha256).digest())
    assert signature == expected.rstrip(b"=").decode()


def test_kling_text_and_first_frame(settings, tmp_path):
    settings.providers["video_gen"] = {"kling": {"access_key": "ak", "secret_key": "sk"}}
    server = _kling_server()
    kling = _provider("kling", settings, server)
    tasks = []
    kling.generate("产品特写", aspect="9:16", duration=4.2, output=tmp_path / "v", on_task=tasks.append)
    assert tasks == ["text2video:kt1"] and "/v1/videos/text2video" in str(server.requests[0].url)
    body = server.bodies()[0]
    assert body == {"prompt": "产品特写", "mode": "pro", "model_name": "kling-v3", "duration": "5", "sound": "off", "aspect_ratio": "9:16"}
    assert server.requests[0].headers["Authorization"].startswith("Bearer ey")
    assert "/v1/videos/text2video/kt1" in str(server.requests[1].url)

    image = make_image(tmp_path / "p.png")
    kling.generate("x", aspect="9:16", duration=5, output=tmp_path / "w", reference_images=[image])
    second = server.bodies()[1]
    assert "/image2video" in str(server.requests[3].url) and not second["image"].startswith("data:")  # 参考图作为首帧
    assert kling.resume("image2video:kt1", output=tmp_path / "r").read_bytes() == b"mp4-bytes"


def test_kling_multi_image_reference_and_fallback(settings, tmp_path):
    settings.providers["video_gen"] = {"kling": {"access_key": "ak", "secret_key": "sk", "reference_model": "kling-v1-6"}}
    images = [make_image(tmp_path / f"{i}.png", size=f"{320 + i}x320") for i in range(5)]
    server = _kling_server()
    kling = _provider("kling", settings, server)
    assert kling.max_reference_images == 4
    kling.generate("x", aspect="9:16", duration=7, output=tmp_path / "v", reference_images=images)
    body = server.bodies()[0]
    assert body["model_name"] == "kling-v1-6" and len(body["image_list"]) == 4 and body["duration"] == "10"
    from shotwright.providers.base import ReferenceImagesUnsupported

    rejected = _kling_server(reject=True)
    kling = _provider("kling", settings, rejected)
    with pytest.raises(ReferenceImagesUnsupported):  # 交给调用方重拼提示词、改用首帧
        kling.generate("x", aspect="9:16", duration=5, output=tmp_path / "w", reference_images=images)
    assert len(rejected.requests) == 1


def test_kling_business_error_is_a_rejection(settings, tmp_path):
    settings.providers["video_gen"] = {"kling": {"access_key": "ak", "secret_key": "sk"}}
    server = Server({("POST", "/v1/videos/"): httpx.Response(200, json={"code": 1102, "message": "账户余额不足"})})
    with pytest.raises(ProviderError, match="余额不足"):
        _provider("kling", settings, server).generate("x", aspect="9:16", duration=5, output=tmp_path / "v")


# ---- MiniMax ------------------------------------------------------------------


def _minimax_server():
    return Server(
        {
            ("POST", "/v1/video_generation"): httpx.Response(200, json={"task_id": "mt1", "base_resp": {"status_code": 0}}),
            ("POST", "/v2/video_generation"): httpx.Response(200, json={"task_id": "mh3", "base_resp": {"status_code": 0}}),
            ("GET", "/v1/query/video_generation"): httpx.Response(
                200, json={"task_id": "mt1", "status": "Success", "file_id": "f1", "base_resp": {"status_code": 0}}
            ),
            ("GET", "/v1/files/retrieve"): httpx.Response(200, json={"file": {"download_url": "https://cdn.example/m.mp4"}}),
            ("GET", "/v2/query/video_generation/mh3"): httpx.Response(
                200, json={"task": {"status": "succeeded", "content": {"url": "https://cdn.example/h3.mp4"}}}
            ),
        }
    )


def test_minimax_hailuo_v1_flow(settings, tmp_path):
    settings.providers["video_gen"] = {"minimax": {"api_key": "k"}}
    server = _minimax_server()
    minimax = _provider("minimax", settings, server)
    image = make_image(tmp_path / "p.png")
    tasks = []
    minimax.generate("x", aspect="9:16", duration=7, output=tmp_path / "v", first_frame=image, on_task=tasks.append)
    body = server.bodies()[0]
    assert tasks == ["v1:mt1"] and body["model"] == "MiniMax-Hailuo-2.3" and body["duration"] == 10
    assert body["resolution"] == "768P" and body["prompt_optimizer"] is False
    assert body["first_frame_image"].startswith("data:image/png;base64,")
    assert "file_id=f1" in str(server.requests[2].url)  # 用 file_id 换下载地址


def test_minimax_1080p_is_six_seconds_only(settings):
    settings.providers["video_gen"] = {"minimax": {"api_key": "k", "resolution": "1080p"}}
    minimax = registry.create("video_gen", "minimax", settings)
    assert minimax.max_duration == 6
    assert minimax.plan("x", aspect="9:16", duration=9, first_frame=None, references=None).seconds == 6


def test_minimax_h3_reference_images(settings, tmp_path):
    settings.providers["video_gen"] = {"minimax": {"api_key": "k", "model": "MiniMax-H3"}}
    server = _minimax_server()
    minimax = _provider("minimax", settings, server)
    assert minimax.max_reference_images == 9
    refs = [make_image(tmp_path / f"{i}.png", size=f"{320 + i}x320") for i in range(2)]
    tasks = []
    path = minimax.generate("x", aspect="9:16", duration=5, output=tmp_path / "v", reference_images=refs, on_task=tasks.append)
    body = server.bodies()[0]
    assert tasks == ["v2:mh3"] and path.read_bytes() == b"mp4-bytes"
    assert [item.get("role") for item in body["content"]] == [None, "reference_image", "reference_image"]
    assert body["ratio"] == "9:16" and body["resolution"] == "768P" and body["duration"] == 5


def test_minimax_business_error_in_http_200(settings, tmp_path):
    settings.providers["video_gen"] = {"minimax": {"api_key": "k"}}
    server = Server(
        {("POST", "/v1/video_generation"): httpx.Response(200, json={"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}})}
    )
    with pytest.raises(ProviderError, match="insufficient balance"):
        _provider("minimax", settings, server).generate("x", aspect="9:16", duration=6, output=tmp_path / "v")


# ---- fal.ai -------------------------------------------------------------------


def _fal_server(error=None):
    status = {"status": "COMPLETED", **({"error": error} if error else {})}
    return Server(
        {
            ("POST", "queue.fal.run/fal-ai/"): httpx.Response(200, json={"request_id": "r1", "status_url": "x", "response_url": "y"}),
            ("GET", "/requests/r1/status"): httpx.Response(200, json=status),
            ("GET", "/requests/r1"): httpx.Response(200, json={"video": {"url": "https://cdn.example/f.mp4"}}),
        }
    )


def test_fal_text_and_image_models(settings, tmp_path):
    settings.providers["video_gen"] = {
        "fal": {
            "api_key": "fk",
            "model": "fal-ai/kling-video/v2.1/standard/text-to-video",
            "image_model": "fal-ai/kling-video/v2.1/standard/image-to-video",
            "durations": "5,10",
            "extra": '{"cfg_scale": 0.5}',
            "price": "0.5",
        }
    }
    server = _fal_server()
    fal = _provider("fal", settings, server)
    tasks = []
    fal.generate("a coat", aspect="9:16", duration=6, output=tmp_path / "v", on_task=tasks.append)
    assert tasks == ["fal-ai/kling-video|r1"]
    assert str(server.requests[0].url).endswith("/fal-ai/kling-video/v2.1/standard/text-to-video")
    assert server.requests[0].headers["Authorization"] == "Key fk"
    assert server.bodies()[0] == {"prompt": "a coat", "duration": "10", "aspect_ratio": "9:16", "cfg_scale": 0.5}
    assert str(server.requests[1].url) == "https://queue.fal.run/fal-ai/kling-video/requests/r1/status"
    fal.generate("a coat", aspect="9:16", duration=5, output=tmp_path / "w", first_frame=make_image(tmp_path / "p.png"))
    assert server.requests[4].url.path.endswith("image-to-video") and "image_url" in server.bodies()[1]


def test_fal_failure_and_config_checks(settings, tmp_path):
    settings.providers["video_gen"] = {"fal": {"api_key": "fk", "model": "fal-ai/veo3/fast"}}
    with pytest.raises(ProviderError, match="content policy"):
        _provider("fal", settings, _fal_server(error="content policy")).generate(
            "x", aspect="9:16", duration=5, output=tmp_path / "v"
        )
    settings.providers["video_gen"]["fal"]["extra"] = "{bad"
    with pytest.raises(ProviderError, match="JSON"):
        registry.create("video_gen", "fal", settings)
    settings.providers["video_gen"]["fal"] = {"api_key": "fk", "model": "fal-ai/veo3/fast", "max_references": "3"}
    with pytest.raises(ProviderError, match="references_field"):
        registry.create("video_gen", "fal", settings)


def test_cost_estimate_uses_model_prices(settings):
    from shotwright.services import cost

    settings.providers["video_gen"] = {
        "wan": {"api_key": "k", "resolution": "720p"},
        "minimax": {"api_key": "k", "model": "MiniMax-H3", "resolution": "2k"},
        "kling": {"access_key": "a", "secret_key": "s", "price": "0.8"},
        "fal": {"api_key": "k", "model": "fal-ai/x", "price": "0.5", "durations": "5,10"},
    }
    assert cost.video_price(settings, "wan") == 0.6
    assert cost.video_price(settings, "wan", "480p") == 0.3
    assert cost.video_price(settings, "minimax") == round(0.13 * 7.2, 3)
    assert cost.video_price(settings, "kling") == 0.8  # 手填的单价优先
    assert cost.video_price(settings, "fal") == 0.5
    assert cost._draft_resolution(settings, "wan") == "480p"
    from shotwright.core.models import Project, Shot

    project, shot = Project(), Shot(text="一句话")
    assert cost._shot_duration(project, shot, settings, "fal", 6.2) == 10.0
    assert cost._shot_duration(project, shot, settings, "wan", 6.2) == 7.0


def test_generation_flow_uses_each_models_reference_ability(client, settings, tmp_path, monkeypatch):
    """同一个镜头：万相把商品图作为参考图提交；可灵不支持参考图，改用商品图作为首帧。"""
    from shotwright.providers.video_gen.kling import KlingVideo
    from shotwright.providers.video_gen.wan import WanVideo

    created = client.post("/api/v2/projects", json={"mode": "split", "script": "一句话。", "voice": {"provider": "silent"}}).json()
    pid, sid = created["id"], created["shots"][0]["id"]
    with make_image(tmp_path / "coat.png").open("rb") as handle:
        image = client.post(f"/api/v2/projects/{pid}/assets", files={"file": ("coat.png", handle)}).json()["id"]
    look = {"subjects": [{"code": "P1", "name": "羽绒服", "kind": "product", "appearance": "立领无帽", "images": [image]}],
            "scene": "", "style": "", "avoid": ""}
    client.patch(f"/api/v2/projects/{pid}", json={"look": look})
    client.patch(f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"mode": "ai", "prompt": "P1 挂在衣架上"}})

    wan_server, kling_server = _wan_server(), _kling_server()
    clip = make_clip(tmp_path / "clip.mp4").read_bytes()
    wan_server.video = kling_server.video = clip
    monkeypatch.setattr(WanVideo, "_client", lambda self: httpx.Client(transport=httpx.MockTransport(wan_server)))
    monkeypatch.setattr(KlingVideo, "_client", lambda self: httpx.Client(transport=httpx.MockTransport(kling_server)))
    settings.providers["video_gen"] = {"wan": {"api_key": "k", "poll_interval": "0"}, "kling": {"access_key": "a", "secret_key": "s", "poll_interval": "0"}}

    for provider in ("wan", "kling"):
        client.patch(f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"provider": provider}})
        response = client.post(f"/api/v2/projects/{pid}/shots/{sid}/candidates", json={"confirm_charge": True})
        assert response.status_code == 200, response.text

    wan_body = wan_server.bodies()[0]
    assert [m["type"] for m in wan_body["input"]["media"]] == ["reference_image"]
    assert "参考图 1 = P1 羽绒服" in wan_body["input"]["prompt"] and "有没有帽子" in wan_body["input"]["prompt"]
    kling_body = kling_server.bodies()[0]
    assert "/image2video" in str(kling_server.requests[0].url) and kling_body["image"]
    project = client.get(f"/api/v2/projects/{pid}").json()
    assert project["cost_credits"] > 0  # 两次生成都按模型单价记了费用


def test_kling_accepts_single_api_key_and_new_result_shape(settings, tmp_path):
    """可灵新的单密钥鉴权（Bearer API Key）和新结构的查询结果（status / outputs[]）。"""
    from shotwright.providers.video_gen.kling import KlingVideo

    spec = registry.spec("video_gen", "kling")
    assert not registry.is_configured(spec, settings)
    settings.providers["video_gen"] = {"kling": {"access_key": "ak"}}
    assert not registry.is_configured(spec, settings)  # 只有 Access Key 不够
    settings.providers["video_gen"] = {"kling": {"api_key": "key-123"}}
    assert registry.is_configured(spec, settings)

    clip = make_clip(tmp_path / "clip.mp4").read_bytes()
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t9", "status": "queued"})
        if "/text2video/t9" in str(request.url):
            return httpx.Response(200, json={"id": "t9", "status": "succeeded", "outputs": [{"type": "video", "url": "https://cdn.example/v.mp4"}]})
        return httpx.Response(200, content=clip)

    provider = KlingVideo({"api_key": "key-123", "poll_interval": "0"})
    provider._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    path = provider.generate("P1 在雪地", aspect="9:16", duration=5, output=tmp_path / "out")
    assert path.exists() and seen[0].headers["Authorization"] == "Bearer key-123"


def test_account_problems_are_not_mistaken_for_unsupported_references(tmp_path):
    """余额不足、Key 无效被拒时不要「去掉参考图重试」——再交一次也一样失败。"""
    from shotwright.providers.base import ReferenceImagesUnsupported
    from shotwright.providers.video_gen.common import is_account_problem
    from shotwright.providers.video_gen.minimax import MiniMaxVideo

    assert is_account_problem("MiniMax 拒绝了请求：HTTP 402，insufficient balance")
    assert is_account_problem("拒绝了请求：HTTP 401，InvalidApiKey")
    assert not is_account_problem("拒绝了请求：HTTP 400，reference_image not supported")
    images = [make_image(tmp_path / "a.png")]

    def run(status, message):
        def handler(request):
            return httpx.Response(status, json={"base_resp": {"status_code": 1008, "status_msg": message}})

        provider = MiniMaxVideo({"api_key": "k", "model": "MiniMax-H3", "poll_interval": "0"})
        provider._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
        provider.generate("x", aspect="9:16", duration=5, output=tmp_path / "o", reference_images=images)

    with pytest.raises(ProviderError) as info:
        run(402, "insufficient balance")
    assert not isinstance(info.value, ReferenceImagesUnsupported)
    with pytest.raises(ReferenceImagesUnsupported):
        run(400, "invalid parameter")


def test_minimax_models_and_draft_resolution(settings):
    """H3 全系列的分辨率；Hailuo 1080P 只有 6 秒，草稿档降到 768P 后恢复 10 秒；费用估算和生成用同一个草稿档。"""
    from shotwright.providers.video_gen.common import draft_resolution
    from shotwright.providers.video_gen.minimax import MiniMaxVideo
    from shotwright.services import cost, visuals

    assert MiniMaxVideo({"api_key": "k", "model": "MiniMax-H3-Lite", "resolution": "480p"}).resolutions == ("480p", "768p")
    assert MiniMaxVideo({"api_key": "k", "model": "MiniMax-H3-Max", "resolution": "768p"}).min_duration == 5
    hailuo = MiniMaxVideo({"api_key": "k", "model": "MiniMax-Hailuo-2.3", "resolution": "1080p"})
    assert hailuo.max_duration == 6
    settings.providers["video_gen"] = {"minimax": {"api_key": "k", "resolution": "1080p", "draft_resolution": "512p"}}
    assert visuals.apply_quality(settings, hailuo, "minimax", "draft") == "768p" and hailuo.max_duration == 10
    assert cost._draft_resolution(settings, "minimax") == "768p"  # Hailuo 2.3 没有 512p，估算也按 768p
    assert draft_resolution("2k", "512p", ("768p", "2k")) == "768p"
    assert draft_resolution("768p", "512p", ("768p", "2k")) == "768p"


def test_shot_falls_back_to_default_when_its_video_service_lost_its_key(settings):
    from shotwright.core.models import Shot, ShotVisual
    from shotwright.services import visuals

    settings.defaults["video_gen"] = "wan"
    settings.providers["video_gen"] = {"wan": {"api_key": "k"}}
    shot = Shot(text="x", visual=ShotVisual(mode="ai", prompt="p", provider="kling"))
    assert visuals._provider_id(settings, "video_gen", shot) == "wan"  # 可灵没配 Key
    settings.providers["video_gen"]["kling"] = {"api_key": "kk"}
    assert visuals._provider_id(settings, "video_gen", shot) == "kling"


def test_fal_can_omit_aspect_ratio(tmp_path):
    from shotwright.providers.video_gen.common import Request
    from shotwright.providers.video_gen.fal import FalVideo

    plain = FalVideo({"api_key": "k", "model": "fal-ai/x/text-to-video"})
    assert plain.build_payload(Request("p", "text", 5, "9:16"))[1]["aspect_ratio"] == "9:16"
    bare = FalVideo({"api_key": "k", "model": "fal-ai/x/text-to-video", "aspect_field": "none"})
    assert "aspect_ratio" not in bare.build_payload(Request("p", "text", 5, "9:16"))[1]


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