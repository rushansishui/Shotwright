import httpx
import pytest

from shotwright.providers.base import ProviderError
from shotwright.providers.stock import pixabay
from shotwright.providers.stock.pixabay import PixabayStock, pick_rendition


def _videos(**sizes):
    base = {
        "tiny": (960, 540, 1_000_000),
        "small": (1280, 720, 3_000_000),
        "medium": (1920, 1080, 9_000_000),
        "large": (3840, 2160, 60_000_000),
    }
    base.update(sizes)
    return {
        name: {
            "url": f"https://cdn.pixabay.com/{name}.mp4" if w else "",
            "width": w,
            "height": h,
            "size": s,
            "thumbnail": f"https://cdn.pixabay.com/{name}.jpg",
        }
        for name, (w, h, s) in base.items()
    }


def test_pick_rendition_by_aspect():
    assert pick_rendition(_videos(), "16:9")[0].endswith("small.mp4")
    assert pick_rendition(_videos(), "9:16")[0].endswith("medium.mp4")


def test_pick_rendition_falls_back_to_largest_available():
    videos = _videos(medium=(1280, 720, 5), large=(0, 0, 0))
    assert pick_rendition(videos, "9:16")[0].endswith("medium.mp4")


def _client_returning(monkeypatch, status, payload, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=payload)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(PixabayStock, "_client", lambda self: httpx.Client(transport=transport))


def test_search_parses_hits_and_skips_short_clips(monkeypatch):
    seen = []
    payload = {
        "hits": [
            {"id": 1, "duration": 2, "videos": _videos(), "user": "a", "pageURL": "https://pixabay.com/v/1"},
            {
                "id": 2,
                "duration": 12,
                "videos": _videos(),
                "user": "bob",
                "pageURL": "https://pixabay.com/v/2",
            },
        ]
    }
    _client_returning(monkeypatch, 200, payload, seen)
    results = PixabayStock({"api_key": "secret"}).search("city night", aspect="9:16", min_duration=5, limit=3)
    assert [r.remote_id for r in results] == ["2"]
    assert results[0].height == 1080 and results[0].meta["author"] == "bob"
    assert results[0].preview_url.endswith("tiny.jpg")
    assert seen[0].url.params["q"] == "city night"


@pytest.mark.parametrize("status", [400, 403])
def test_search_reports_bad_key_without_leaking_it(monkeypatch, status):
    _client_returning(monkeypatch, status, {})
    with pytest.raises(ProviderError) as info:
        PixabayStock({"api_key": "secret-key"}).search("x", aspect="9:16", min_duration=1, limit=1)
    assert "secret-key" not in str(info.value)


def test_registered_and_requires_key(settings):
    from shotwright.providers import registry
    from shotwright.providers.base import ProviderNotConfigured

    assert any(s.id == "pixabay" for s in registry.specs("stock"))
    with pytest.raises(ProviderNotConfigured):
        registry.create("stock", "pixabay", settings)
    assert pixabay.API_URL.startswith("https://pixabay.com/")
