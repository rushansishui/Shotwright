import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.promo.make_promo import copy_demo, grid_clip
from shotwright.media.ffmpeg import probe, run_ffmpeg

ROOT = Path(__file__).resolve().parents[1]


def test_promo_rejects_work_directly_inside_source(tmp_path):
    source = tmp_path / "projects"
    source.mkdir()
    marker = source / "keep.txt"
    marker.write_text("original", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/promo/make_promo.py"),
            "--source",
            str(source),
            "--work",
            str(source / "promos"),
            "--demo",
            "example",
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert b"--work" in result.stderr
    assert marker.read_text(encoding="utf-8") == "original"
    assert not (source / "promos").exists()


def test_promo_copy_leaves_pending_tasks_in_source_only(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    project = {"shots": [{"visual": {"pending": {"task_id": "original"}}}]}
    (source / "project.json").write_text(json.dumps(project), encoding="utf-8")
    copied = tmp_path / "copied"
    copy_demo(source, copied)
    assert json.loads((source / "project.json").read_text(encoding="utf-8")) == project
    assert json.loads((copied / "project.json").read_text(encoding="utf-8")) == {"shots": [{"visual": {}}]}


@pytest.mark.parametrize(("vertical", "size"), [(False, (1920, 1080)), (True, (1080, 1920))])
def test_promo_grid_accepts_mixed_aspect_ratios(tmp_path, monkeypatch, vertical, size):
    monkeypatch.chdir(ROOT)
    left, right, result = (tmp_path / f"{name}.mp4" for name in ("left", "right", "out"))
    for path, resolution in ((left, "320x180"), (right, "180x320")):
        run_ffmpeg(["-f", "lavfi", "-i", f"color=size={resolution}:duration=1", str(path)])
    grid_clip(
        left,
        right,
        ("Chinese", "English"),
        0.2,
        result,
        Path("resources/fonts/NotoSansSC-Bold.ttf"),
        vertical=vertical,
    )
    info = probe(result)
    assert (info.width, info.height) == size
