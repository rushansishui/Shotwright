"""新增的收费配音服务：Azure、硅基流动、MiniMax、ElevenLabs。"""

from concurrent.futures import Executor, Future

import pytest
from fastapi.testclient import TestClient

from shotwright.api.app import create_app
from shotwright.api.jobs import JobManager
from shotwright.core.store import ProjectStore
from shotwright.providers import registry


class _InlineExecutor(Executor):
    def submit(self, fn, *args, **kwargs):
        future: Future = Future()
        future.set_result(fn(*args, **kwargs))
        return future


@pytest.fixture
def client(settings):
    store = ProjectStore(settings.storage_dir)
    app = create_app(settings, job_manager=JobManager(store, settings, executor=_InlineExecutor()))
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("provider_id", ["azure", "siliconflow", "minimax", "elevenlabs"])
def test_new_tts_providers_are_registered(provider_id):
    spec = registry.spec("tts", provider_id)
    assert spec.paid is True
    assert spec.voices, "收费配音服务要自带声音列表，否则界面上选不了声音"
    assert any(f.key == "api_key" and f.secret for f in spec.fields)


def test_azure_builds_ssml_with_escaped_text():
    from shotwright.providers.tts.azure import build_ssml

    ssml = build_ssml("a & b <c>", "zh-CN-XiaoxiaoNeural", 1.2, 1.0)
    assert "&amp;" in ssml and "&lt;c&gt;" in ssml
    assert 'rate="+20%"' in ssml
    assert "zh-CN-XiaoxiaoNeural" in ssml


def test_new_tts_rejects_bad_config(settings):
    from shotwright.providers.base import ProviderError

    settings.providers["tts"] = {"azure": {"api_key": "k", "region": "  "}}
    with pytest.raises(ProviderError, match="配置有误"):
        registry.create("tts", "azure", settings)


def test_voices_endpoint_switches_by_provider(client):
    edge = client.get("/api/v2/voices", params={"language": "zh-CN"}).json()
    assert edge and all(v["id"].startswith("zh-") for v in edge)
    azure = client.get("/api/v2/voices", params={"provider": "azure"}).json()
    assert azure and azure[0]["id"] == "zh-CN-XiaoxiaoNeural"


def _fake_edge(monkeypatch, failures: int):
    """把 edge_tts.Communicate 换成前几次超时、之后正常返回的假实现，并跳过音频解析。"""
    import edge_tts

    from shotwright.media.ffmpeg import ProbeResult
    from shotwright.providers.tts import edge

    calls = {"n": 0}

    class FakeCommunicate:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self):
            calls["n"] += 1
            if calls["n"] <= failures:
                raise TimeoutError
            yield {"type": "audio", "data": b"mp3"}
            yield {"type": "WordBoundary", "offset": 0, "duration": 5_000_000, "text": "hi"}

    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)
    monkeypatch.setattr(
        edge,
        "probe",
        lambda path: ProbeResult(duration=0.5, width=None, height=None, has_video=False, has_audio=True),
    )
    return calls


def test_edge_retries_after_timeout(monkeypatch, tmp_path):
    from shotwright.providers.tts.edge import EdgeTTS

    calls = _fake_edge(monkeypatch, failures=2)
    tts = EdgeTTS({"retries": "3", "retry_wait": "0"})
    result = tts.synthesize("hi", voice="en-US-AriaNeural", rate=1.0, volume=1.0, output=tmp_path / "a.mp3")
    assert calls["n"] == 3
    assert result.duration == 0.5 and result.words[0].text == "hi"


def test_edge_gives_up_with_hint(monkeypatch, tmp_path):
    from shotwright.providers.base import ProviderError
    from shotwright.providers.tts.edge import EdgeTTS

    calls = _fake_edge(monkeypatch, failures=10)
    tts = EdgeTTS({"retries": "2", "retry_wait": "0"})
    with pytest.raises(ProviderError, match=r"TimeoutError（试了 3 次）.*proxy"):
        tts.synthesize("hi", voice="en-US-AriaNeural", rate=1.0, volume=1.0, output=tmp_path / "a.mp3")
    assert calls["n"] == 3
    assert not (tmp_path / "a.mp3").exists()
