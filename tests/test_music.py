"""背景音乐的授权分类与目录归属。"""

import pytest

from shotwright.providers.music.archive import license_kind, parse_length


@pytest.mark.parametrize(
    "url,kind",
    [
        ("http://creativecommons.org/publicdomain/zero/1.0/", "cc0"),
        ("http://creativecommons.org/licenses/by/4.0/", "cc-by"),
        ("http://creativecommons.org/licenses/by-sa/4.0/", "cc-by-sa"),
        ("http://creativecommons.org/licenses/by-nc/4.0/", "restricted"),
        ("http://creativecommons.org/publicdomain/mark/1.0/", "public-domain"),
        ("", "unknown"),
    ],
)
def test_license_kind(url, kind):
    assert license_kind(url) == kind


def test_license_kind_rejects_noncommercial():
    """非商用授权要被归到 restricted，检索时会被丢掉。"""
    assert license_kind("https://creativecommons.org/licenses/by-nc-nd/4.0/") == "restricted"


@pytest.mark.parametrize(
    "raw,want", [("3725.53", 3725.53), ("4:12", 252.0), ("1:00:05", 3605.0), ("", None)]
)
def test_parse_length(raw, want):
    assert parse_length(raw) == want


def test_music_lands_in_its_own_folder(settings, store):
    from shotwright.core.models import Project

    project = store.create(Project())
    asset = store.add_asset(
        project,
        _make_mp3(store, project),
        kind="audio",
        source="archive",
        suffix=".mp3",
        folder="music",
        move=True,
    )
    assert asset.folder == "music"
    assert "music" in str(store.asset_path(project, asset.id))


def _make_mp3(store, project):
    from pathlib import Path

    from shotwright.media.ffmpeg import run_ffmpeg

    path = Path(store.cache_dir(project.id, "tmp")) / "t.mp3"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "libmp3lame", str(path)])
    return path
