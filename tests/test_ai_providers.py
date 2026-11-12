import base64
import json

import httpx
import pytest
from conftest import FakeImageGen, FakeVideoGen, make_image

from shotwright.core.models import Project, Shot, ShotVisual, VoiceSettings
from shotwright.providers import paid_task, registry
from shotwright.providers.base import ChargeConfirmationRequired, ProviderError, UnconfirmedTaskError
from shotwright.providers.image_gen.openai_images import SEEDREAM_SIZES, OpenAIImages
from shotwright.providers.video_gen.seedance import SeedanceVideo, clamp_duration
from shotwright.services import visuals

KEY = "ark-secret-key"


def _seedance(handler, **extra) -> SeedanceVideo:
    provider = SeedanceVideo({"api_key": KEY, "poll_interval": "0", **extra})
    provider._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return provider


def test_clamp_duration():
    assert clamp_duration(2.3, 4, 30) == 4
    assert clamp_duration(6.2, 4, 30) == 7
    assert clamp_duration(6.0, 4, 30) == 6
    assert clamp_duration(99, 4, 30) == 30


def test_seedance_submit_poll_download(tmp_path):
    frame = make_image(tmp_path / "frame.png")
    seen = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.headers["Authorization"] == f"Bearer {KEY}"
            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"id": "cgt-1"})
        if request.url.path.endswith("/tasks/cgt-1"):
            seen["polls"] += 1
            if seen["polls"] < 3:
                return httpx.Response(200, json={"status": "running"})
            return httpx.Response(
                200, json={"status": "succeeded", "content": {"video_url": "https://cdn.example/v.mp4"}}
            )
        assert str(request.url) == "https://cdn.example/v.mp4"
        return httpx.Response(200, content=b"mp4-bytes")

    out = _seedance(handler).generate(
        "海边日落", aspect="9:16", duration=5.4, output=tmp_path / "gen", first_frame=frame
    )
    assert out.read_bytes() == b"mp4-bytes" and out.suffix == ".mp4"
    payload = seen["payload"]
    assert payload["duration"] == 6 and payload["ratio"] == "adaptive"
    assert payload["model"] == "doubao-seedance-2-5-260628"
    assert payload["content"][1]["role"] == "first_frame"
    assert payload["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert seen["polls"] == 3


def test_seedance_text_only_uses_canvas_ratio(tmp_path):
    payload = _seedance(lambda r: httpx.Response(500)).build_payload(
        "city", aspect="16:9", duration=3, first_frame=None
    )
    assert payload["ratio"] == "16:9" and len(payload["content"]) == 1 and payload["duration"] == 4


@pytest.mark.parametrize("response", [httpx.Response(502), httpx.Response(200, text="not json")])
def test_seedance_submit_is_never_retried(tmp_path, response):
    calls = []

    def handler(request):
        calls.append(request.method)
        return response

    with pytest.raises(UnconfirmedTaskError):
        _seedance(handler).generate("x", aspect="9:16", duration=4, output=tmp_path / "gen")
    assert calls == ["POST"]


def test_seedance_submit_timeout_is_unconfirmed(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.method)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(UnconfirmedTaskError, match="可能已经创建任务"):
        _seedance(handler).generate("x", aspect="9:16", duration=4, output=tmp_path / "gen")
    assert calls == ["POST"]


def test_seedance_rejection_is_definite_and_redacted(tmp_path):
    def handler(request):
        return httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}})

    with pytest.raises(ProviderError) as info:
        _seedance(handler).generate("x", aspect="9:16", duration=4, output=tmp_path / "gen")
    assert not isinstance(info.value, UnconfirmedTaskError)
    assert KEY not in str(info.value)


def test_seedance_failed_task(tmp_path):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t"})
        return httpx.Response(200, json={"status": "failed", "error": {"message": "内容审核未通过"}})

    with pytest.raises(ProviderError, match="内容审核未通过"):
        _seedance(handler).generate("x", aspect="9:16", duration=4, output=tmp_path / "gen")


def test_poll_retries_transient_errors_then_gives_up():
    responses = iter([httpx.Response(503), httpx.Response(200, json={"status": "succeeded", "ok": 1})])
    client = httpx.Client(transport=httpx.MockTransport(lambda r: next(responses)))
    settings = paid_task.PollSettings(label="T", interval=0)
    body = paid_task.poll(
        client, "https://x/t", task_id="t", headers={}, secret="", settings=settings, sleep=lambda s: None
    )
    assert body["ok"] == 1

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(UnconfirmedTaskError, match="任务 id：t"):
        paid_task.poll(
            client, "https://x/t", task_id="t", headers={}, secret="", settings=settings, sleep=lambda s: None
        )


def test_poll_deadline():
    ticks = iter(range(0, 10_000, 100))
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "queued"}))
    )
    settings = paid_task.PollSettings(label="T", interval=0, timeout=250)
    with pytest.raises(UnconfirmedTaskError, match="超过等待时间"):
        paid_task.poll(
            client,
            "https://x/t",
            task_id="t",
            headers={},
            secret="",
            settings=settings,
            sleep=lambda s: None,
            clock=lambda: next(ticks),
        )


def _images() -> OpenAIImages:
    settings = {
        "api_key": KEY,
        "model": "doubao-seedream-5-0-pro-260628",
        "base_url": "https://ark.example/api/v3",
    }
    return OpenAIImages(settings, label="Seedream", sizes=SEEDREAM_SIZES)


def test_seedream_b64_and_url(tmp_path, monkeypatch):
    png = make_image(tmp_path / "src.png").read_bytes()
    seen = {}

    def handler(request):
        if request.method == "POST":
            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png).decode()}]})
        return httpx.Response(404)

    real = httpx.Client
    monkeypatch.setattr(
        "shotwright.providers.image_gen.openai_images.httpx.Client",
        lambda **kw: real(transport=httpx.MockTransport(handler)),
    )
    provider = _images()
    out = provider.generate("一只猫", aspect="9:16", output=tmp_path / "gen")
    assert out.read_bytes() == png
    assert seen["payload"]["size"] == "1440x2560" and seen["payload"]["watermark"] is False

    def url_handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/a.jpeg?sig=1"}]})
        return httpx.Response(200, content=png)

    monkeypatch.setattr(
        "shotwright.providers.image_gen.openai_images.httpx.Client",
        lambda **kw: real(transport=httpx.MockTransport(url_handler)),
    )
    out = provider.generate("一只猫", aspect="16:9", output=tmp_path / "gen2")
    assert out.suffix == ".jpg" and out.read_bytes() == png


def test_seedream_5xx_is_unconfirmed(tmp_path, monkeypatch):
    real = httpx.Client
    monkeypatch.setattr(
        "shotwright.providers.image_gen.openai_images.httpx.Client",
        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    with pytest.raises(UnconfirmedTaskError):
        _images().generate("x", aspect="1:1", output=tmp_path / "gen")


def test_ark_key_is_shared_from_doubao(settings):
    settings.providers.setdefault("llm", {})["doubao"] = {"api_key": "shared"}
    spec = registry.spec("video_gen", "seedance")
    assert registry.is_configured(spec, settings)
    assert registry.create("video_gen", "seedance", settings).api_key == "shared"
    settings.providers.setdefault("video_gen", {})["seedance"] = {"api_key": "own"}
    assert registry.create("video_gen", "seedance", settings).api_key == "own"


def _shot_project(store, mode, **visual):
    shot = Shot(text="测试镜头。", visual=ShotVisual(mode=mode, prompt="a cat", **visual))
    project = store.create(Project(voice=VoiceSettings(provider="silent"), shots=[shot]))
    return project, project.shots[0]


def test_generation_requires_confirmation(store, settings):
    project, shot = _shot_project(store, "ai")
    with pytest.raises(ChargeConfirmationRequired):
        visuals.refresh_candidates(store, settings, project, shot)
    assert FakeVideoGen.calls == []


def test_image_then_video_uses_first_frame(store, settings):
    project, shot = _shot_project(store, "image", provider="pixabay")
    [image_id] = visuals.refresh_candidates(store, settings, project, shot, confirm_charge=True)
    assert project.assets[image_id].kind == "image" and FakeImageGen.calls[0].startswith("a cat\n")
    assert shot.visual.selected == image_id

    visuals.switch_mode(project, shot, "ai")
    assert shot.visual.first_frame == image_id and shot.visual.selected is None
    [video_id] = visuals.refresh_candidates(store, settings, project, shot, confirm_charge=True)
    assert FakeVideoGen.calls[0]["first_frame"] == store.asset_path(project, image_id)
    assert project.assets[video_id].meta["first_frame"] == image_id
    assert shot.visual.selected == video_id


def test_seedance_rejects_bad_config(settings):
    settings.providers.setdefault("llm", {})["doubao"] = {"api_key": "k"}
    settings.providers.setdefault("video_gen", {})["seedance"] = {"resolution": "4k"}
    with pytest.raises(ProviderError, match="配置有误"):
        registry.create("video_gen", "seedance", settings)
    settings.providers["video_gen"]["seedance"] = {"max_duration": "abc"}
    with pytest.raises(ProviderError, match="配置有误"):
        registry.create("video_gen", "seedance", settings)


def test_first_frame_mime_is_sniffed(tmp_path):
    jpeg = tmp_path / "frame.png"  # 扩展名和内容不一致
    from shotwright.media.ffmpeg import run_ffmpeg

    run_ffmpeg(
        ["-f", "lavfi", "-i", "testsrc2=size=64x64:rate=1", "-frames:v", "1", "-f", "mjpeg", str(jpeg)]
    )
    from shotwright.providers.video_gen.seedance import image_data_url

    assert image_data_url(jpeg).startswith("data:image/jpeg;base64,")
    bad = tmp_path / "x.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(ProviderError):
        image_data_url(bad)


def test_seedream_requests_a_single_image():
    payload = _images().build_payload("猫", "9:16")
    assert "n" not in payload and "sequential_image_generation" not in payload
    assert payload["model"] == "doubao-seedream-5-0-pro-260628"


def test_unconfigured_generator_reports_missing_config(store, settings):
    settings.defaults["video_gen"] = "seedance"
    settings.providers.pop("llm", None)
    project, shot = _shot_project(store, "ai")
    with pytest.raises(ProviderError, match="缺少配置"):
        visuals.refresh_candidates(store, settings, project, shot)


def test_model_not_opened_gives_console_hint(tmp_path, monkeypatch):
    real = httpx.Client
    body = {"error": {"code": "InvalidEndpointOrModel.NotFound", "message": "The model does not exist"}}
    monkeypatch.setattr(
        "shotwright.providers.image_gen.openai_images.httpx.Client",
        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(404, json=body))),
    )
    with pytest.raises(ProviderError, match="开通管理") as info:
        _images().generate("x", aspect="9:16", output=tmp_path / "gen")
    assert not isinstance(info.value, UnconfirmedTaskError)


def test_switching_mode_selects_that_sources_candidate(store, settings):
    project, shot = _shot_project(store, "image")
    [image_id] = visuals.refresh_candidates(store, settings, project, shot, confirm_charge=True)
    visuals.switch_mode(project, shot, "stock")
    assert shot.visual.selected is None
    shot.visual.terms = ["sea"]
    [stock_id, *_] = visuals.refresh_candidates(store, settings, project, shot)
    assert visuals.candidates_for_mode(
        project, shot, "stock"
    ) and image_id not in visuals.candidates_for_mode(project, shot, "stock")
    visuals.switch_mode(project, shot, "image")
    assert shot.visual.selected == image_id
    visuals.switch_mode(project, shot, "color")
    assert shot.visual.selected == image_id  # 纯色模式不丢原选择
    visuals.switch_mode(project, shot, "stock")
    assert visuals.asset_mode(project.assets[shot.visual.selected]) == "stock"
    assert stock_id in shot.visual.candidates


# ---- 大模型：传输层断开时的重试 -------------------------------------------------


def _llm(handler, monkeypatch, **extra):
    from shotwright.providers.llm.openai_compat import OpenAICompatibleLLM

    real = httpx.Client
    monkeypatch.setattr(
        "shotwright.providers.llm.openai_compat.httpx.Client",
        lambda **kw: real(transport=httpx.MockTransport(handler)),
    )
    # 重试的退避等待不让测试真的等。
    monkeypatch.setattr("shotwright.providers.llm.openai_compat.time.sleep", lambda seconds: None)
    return OpenAICompatibleLLM({"api_key": KEY, "base_url": "https://llm.test/v1", "model": "m", **extra})


def test_llm_retries_when_connection_drops(monkeypatch):
    """推理模型要等几十秒，中途被网络设备断开时应该自动重试，而不是让用户再点一次。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 3:
            raise httpx.RemoteProtocolError("server disconnected", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    llm = _llm(handler, monkeypatch)
    assert llm.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert len(calls) == 3


def test_llm_reports_actionable_reason_after_retries(monkeypatch):
    """重试用完仍失败时，错误提示要说明发生了什么、去哪里改，而不只是一个异常类名。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("server disconnected", request=request)

    llm = _llm(handler, monkeypatch)
    with pytest.raises(ProviderError) as info:
        llm.complete([{"role": "user", "content": "hi"}])
    message = str(info.value)
    assert "连接被服务端中断" in message
    assert "已重试 2 次" in message
    assert "代理" in message


def test_llm_does_not_retry_read_timeout(monkeypatch):
    """读超时说明服务端可能已经在生成，重试会重复计费，只报错不重试。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    llm = _llm(handler, monkeypatch)
    with pytest.raises(ProviderError, match="ReadTimeout"):
        llm.complete([{"role": "user", "content": "hi"}])
    assert len(calls) == 1


# ---- 大模型：输出被截断 ---------------------------------------------------------


def test_llm_sends_configured_max_tokens(monkeypatch):
    """不传 max_tokens 时服务商默认值很小，30 多个镜头的分镜会被从中间砍断。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    llm = _llm(handler, monkeypatch, max_tokens="16384")
    assert llm.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert seen[0]["max_tokens"] == 16384


def test_llm_leaves_max_tokens_to_the_provider_when_unset(monkeypatch):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    _llm(handler, monkeypatch).complete([{"role": "user", "content": "hi"}])
    assert "max_tokens" not in seen[0]


def test_llm_explains_truncated_output_instead_of_json_error(monkeypatch):
    """输出到上限被截断时，JSON 一定不完整；这时该说「太长了」，而不是「无法解析」。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"shots": [{"text": "半句'}, "finish_reason": "length"}]
            },
        )

    llm = _llm(handler, monkeypatch, max_tokens="8192")
    with pytest.raises(ProviderError) as info:
        llm.complete([{"role": "user", "content": "hi"}], json_mode=True)
    message = str(info.value)
    assert "输出被截断" in message
    assert "8192" in message
    assert "无法解析" not in message


def test_llm_drops_max_tokens_when_the_model_rejects_it(monkeypatch):
    """填的 max_tokens 超过这个模型的上限时，退回到服务商默认值，而不是整次失败。"""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "max_tokens" in body:
            return httpx.Response(400, json={"error": {"message": "max_tokens too large"}})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    llm = _llm(handler, monkeypatch, max_tokens="999999")
    assert llm.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert "max_tokens" in calls[0] and "max_tokens" not in calls[1]
    assert llm.max_tokens == 0  # 之后不再重复尝试
