"""背景音乐检索：文件挑选、检索词语法、检索接口。

这些用例不需要 FFmpeg 或网络：文件列表和检索结果都用固定数据。
"""

from __future__ import annotations

import pytest

from shotwright.core.models import Project
from shotwright.providers.music.archive import ArchiveMusic, pick_audio_file
from shotwright.services.music import build_default_query

# ---- pick_audio_file ---------------------------------------------------------


def _file(name: str, fmt: str = "VBR MP3", length: str = "120.0") -> dict:
    return {"name": name, "format": fmt, "length": length}


def test_pick_audio_file_takes_first_mp3_in_range():
    files = [_file("a.mp3", length="90"), _file("b.mp3", length="150")]
    chosen = pick_audio_file(files, min_duration=60, max_duration=200)
    assert chosen is not None and chosen["name"] == "a.mp3"


def test_pick_audio_file_skips_short_and_long():
    files = [_file("short.mp3", length="10"), _file("long.mp3", length="900"), _file("ok.mp3", length="120")]
    chosen = pick_audio_file(files, min_duration=60, max_duration=600)
    assert chosen is not None and chosen["name"] == "ok.mp3"


def test_pick_audio_file_skips_non_mp3_and_unknown_format():
    files = [
        _file("song.flac"),
        _file("song.ogg"),
        _file("song.mp3", fmt="Ogg Vorbis"),
        _file("no-length.mp3", length=""),
    ]
    assert pick_audio_file(files, min_duration=0, max_duration=600) is None


def test_pick_audio_file_respects_inclusive_bounds():
    assert pick_audio_file([_file("m.mp3", length="60")], min_duration=60, max_duration=600) is not None
    assert pick_audio_file([_file("m.mp3", length="600")], min_duration=60, max_duration=600) is not None
    assert pick_audio_file([_file("m.mp3", length="59.9")], min_duration=60, max_duration=600) is None


def test_pick_audio_file_empty_list():
    assert pick_audio_file([], min_duration=0, max_duration=100) is None


# ---- build_query -------------------------------------------------------------


def test_build_query_includes_media_and_format():
    query = ArchiveMusic({}).build_query("calm piano")
    assert "mediatype:audio" in query
    assert "format:(MP3)" in query
    assert "(calm AND piano)" in query


def test_build_query_splits_on_commas():
    query = ArchiveMusic({}).build_query("calm, piano")
    assert "(calm AND piano)" in query


def test_build_query_without_terms_has_no_empty_clause():
    query = ArchiveMusic({}).build_query("   ")
    assert "()" not in query
    assert query.startswith("mediatype:audio")


def test_build_query_includes_collection_when_configured():
    query = ArchiveMusic({"collection": "opensource_audio"}).build_query("ambient")
    assert "collection:(opensource_audio)" in query


def test_build_query_omits_collection_when_blank():
    assert "collection:" not in ArchiveMusic({"collection": ""}).build_query("ambient")


# ---- build_default_query -----------------------------------------------------


def test_build_default_query_uses_subject():
    assert build_default_query(Project(subject="一款防水登山鞋")) == "一款防水登山鞋"


def test_build_default_query_truncates_subject():
    project = Project(subject="x" * 200)
    assert len(build_default_query(project)) == 60


def test_build_default_query_falls_back_without_subject():
    assert build_default_query(Project()) == "calm background music"


# ---- 授权配置 ----------------------------------------------------------------


@pytest.mark.parametrize("value,want", [("false", False), ("0", False), ("true", True), ("", True)])
def test_allow_unknown_license_flag(value, want):
    assert ArchiveMusic({"allow_unknown_license": value}).allow_unknown is want
