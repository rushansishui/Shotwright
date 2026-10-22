import shutil
from pathlib import Path

import pytest

from shotwright.config import Settings
from shotwright.core.store import ProjectStore
from shotwright.media.ffmpeg import run_ffmpeg
from shotwright.providers import registry
from shotwright.providers.base import ProviderSpec, StockCandidate

if shutil.which("ffmpeg") is None:  # pragma: no cover
    pytest.skip("需要 FFmpeg", allow_module_level=True)


def make_clip(path: Path, *, seconds: float = 2.0, size: str = "720x1280", pattern: str = "testsrc2") -> Path:
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"{pattern}=size={size}:rate=30",
            "-t",
            str(seconds),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    return path


class FakeStock:
    """离线的素材检索：每个检索词\"找到\"两段测试画面。"""

    calls: list[str] = []

    def __init__(self, settings):
        pass

    def search(self, query, *, aspect, min_duration, limit):
        FakeStock.calls.append(query)
        return [
            StockCandidate(
                "fake", f"{query}-{i}", f"fake://{query}/{i}", "video", 720, 1280, 3.0, meta={"query": query}
            )
            for i in range(2)
        ][:limit]

    def download(self, candidate, output):
        pattern = "testsrc2" if candidate.remote_id.endswith("0") else "smptebars"
        return make_clip(output.with_suffix(".mp4"), pattern=pattern)


registry.register(ProviderSpec(kind="stock", id="fake", name="Fake", offline=True), FakeStock)


def make_image(path: Path, *, size: str = "640x360") -> Path:
    run_ffmpeg(["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=1", "-frames:v", "1", str(path)])
    return path


class FakeVideoGen:
    """收费的 AI 视频：记录调用参数，生成一段测试画面。"""

    calls: list[dict] = []

    def __init__(self, settings):
        pass

    def generate(self, prompt, *, aspect, duration, output, first_frame=None):
        FakeVideoGen.calls.append({"prompt": prompt, "duration": duration, "first_frame": first_frame})
        return make_clip(output.with_suffix(".mp4"), pattern="rgbtestsrc")


class FakeImageGen:
    calls: list[str] = []

    def __init__(self, settings):
        pass

    def generate(self, prompt, *, aspect, output):
        FakeImageGen.calls.append(prompt)
        return make_image(output.with_suffix(".png"))


registry.register(ProviderSpec(kind="video_gen", id="fakevid", name="FakeVid", paid=True), FakeVideoGen)
registry.register(ProviderSpec(kind="image_gen", id="fakeimg", name="FakeImg", paid=True), FakeImageGen)


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings(storage_dir=tmp_path / "projects", auto_fetch_visuals=False)
    # TestClient 发出的 Host 是 testserver；真实服务只放行本机地址和配置里的地址。
    s.allowed_hosts = ["testserver"]
    s.defaults.update(
        {"tts": "silent", "stock": "fake", "llm": "", "video_gen": "fakevid", "image_gen": "fakeimg"}
    )
    FakeVideoGen.calls.clear()
    FakeImageGen.calls.clear()
    return s


@pytest.fixture
def store(settings) -> ProjectStore:
    return ProjectStore(settings.storage_dir)
