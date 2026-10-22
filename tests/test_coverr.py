"""Coverr 素材库。"""

import httpx
import pytest

from shotwright.providers.base import ProviderError, StockCandidate
from shotwright.providers.stock.coverr import CoverrStock, _matches_orientation


def test_coverr_search_and_download(monkeypatch, tmp_path):
    listing = {
        "hits": [
            {
                "id": "abc",
                "video_id": "v1",
                "duration": "12.5",
                "is_premium": False,
                "max_width": 1920,
                "max_height": 1080,
                "thumbnail": "https://x/t.jpg",
                "slug": "clip",
                "title": "Clip",
            },
            {"id": "paid", "video_id": "v2", "duration": "12.5", "is_premium": True},
            {"id": "short", "video_id": "v3", "duration": "1.0", "is_premium": False},
        ]
    }
    detail = {"urls": {"mp4_download": "https://cdn.example/v.mp4?download=true"}}

    def handler(request):
        if request.url.path.endswith("/abc"):
            return httpx.Response(200, json=detail)
        return httpx.Response(200, json=listing)

    real = httpx.Client
    monkeypatch.setattr(
        "shotwright.providers.stock.coverr.httpx.Client",
        lambda **kw: real(transport=httpx.MockTransport(handler)),
    )
    results = CoverrStock({}).search("city", aspect="16:9", min_duration=3, limit=5)
    # 付费的和太短的都被筛掉。
    assert [c.remote_id for c in results] == ["v1"]
    assert results[0].url.startswith("https://cdn.example/")


def test_coverr_rejects_html_instead_of_video(monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})

    real = httpx.Client
    monkeypatch.setattr(
        "shotwright.providers.stock.coverr.httpx.Client",
        lambda **kw: real(transport=httpx.MockTransport(handler)),
    )
    candidate = StockCandidate("coverr", "v1", "https://cdn.example/v.mp4", "video", 1920, 1080, 10.0)
    with pytest.raises(ProviderError, match="没有返回视频文件"):
        CoverrStock({}).download(candidate, tmp_path / "out")


@pytest.mark.parametrize(
    "aspect,w,h,expected",
    [
        ("9:16", 1080, 1920, True),
        ("9:16", 1920, 1080, False),
        ("16:9", 1920, 1080, True),
        ("1:1", 1, 9, True),
    ],
)
def test_coverr_orientation(aspect, w, h, expected):
    assert _matches_orientation(w, h, aspect) is expected
