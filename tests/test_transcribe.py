"""剪辑页：识别原声 → 按文字剪 → 原声镜头（不配音，字幕按识别的时间对齐）。"""

import subprocess
import time

import pytest

from shotwright.providers import registry
from shotwright.providers.base import ProviderSpec, Transcript, WordTiming
from shotwright.services import transcribe
from shotwright.services.transcribe import Word, filler_indexes, join_words, plan_ranges

# 假的识别结果：8 秒原片，两句话，中间夹一个"嗯"，第二句前有 2 秒停顿。
WORDS = [
    ("大家好，", 0.5, 1.2),
    ("嗯，", 1.3, 1.6),
    ("今天", 1.8, 2.2),
    ("介绍", 2.2, 2.6),
    ("羽绒服。", 2.6, 3.2),
    ("轻薄", 5.2, 5.6),
    ("保暖。", 5.6, 6.2),
]


class FakeASR:
    calls: list[str | None] = []

    def __init__(self, settings):
        pass

    def transcribe(self, audio, *, language, progress=None):
        FakeASR.calls.append(language)
        assert audio.stat().st_size > 0
        if progress:
            progress(0.5)
        return Transcript(
            words=[WordTiming(*w) for w in WORDS],
            sentences=[(0.5, 3.2), (5.2, 6.2)],
            language="zh",
        )


registry.register(ProviderSpec(kind="asr", id="fakeasr", name="FakeASR", offline=True), FakeASR)


def _words():
    return [Word(*w) for w in WORDS]


def test_plan_ranges_cuts_removed_words_and_long_pauses():
    words = _words()
    # 什么都不删、不压缩停顿：整段保留。
    assert plan_ranges(words, 8.0, set()) == [(0.0, 8.0)]
    # 删掉"嗯"：从"大家好"结尾剪到"今天"开头（各留一点余量），没说话的画面仍保留。
    ranges = plan_ranges(words, 8.0, {1})
    assert len(ranges) == 2
    assert ranges[0][0] == 0.0 and 1.2 <= ranges[0][1] <= 1.3
    assert 1.6 <= ranges[1][0] <= 1.8 and ranges[1][1] == 8.0
    # 再压缩超过 1 秒的停顿：两句之间的 2 秒、结尾 1.8 秒空白剪掉，两边各留 0.25 秒；开头 0.5 秒不到 1 秒，保留。
    ranges = plan_ranges(words, 8.0, {1}, max_pause=1.0)
    assert ranges[0][0] == 0.0
    assert ranges[1][1] == pytest.approx(3.45, abs=0.01) and ranges[2][0] == pytest.approx(4.95, abs=0.01)
    assert ranges[-1][1] == pytest.approx(6.45, abs=0.01)
    kept = sum(b - a for a, b in ranges)
    assert kept < 5.5
    # 全部删掉：没有可保留的内容。
    assert plan_ranges(words, 8.0, set(range(len(words))), max_pause=1.0) == []


def test_fillers_and_joining():
    assert filler_indexes(_words(), "zh-CN") == [1]
    english = [Word("So", 0, 0.2), Word("um,", 0.3, 0.5), Word("this", 0.6, 0.8)]
    assert filler_indexes(english, "en-US") == [1]
    assert join_words(["大家好，", "今天"]) == "大家好，今天"
    assert join_words(["Hello,", "world", "!"]) == "Hello, world!"


def _clip_with_audio(path):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=720x1280:rate=30:duration=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=8",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


def _wait_done(client, pid, asset):
    for _ in range(100):
        data = client.get(f"/api/v2/projects/{pid}/transcripts/{asset}").json()
        if data["state"] != "running":
            return data
        time.sleep(0.1)
    raise AssertionError("识别一直没完成")


def test_cut_flow_builds_original_speech_shots_and_renders(client, settings, store, tmp_path):
    settings.defaults["asr"] = "fakeasr"
    FakeASR.calls.clear()
    pid = client.post("/api/v2/projects", json={"mode": "empty", "voice": {"provider": "silent"}}).json()[
        "id"
    ]
    with _clip_with_audio(tmp_path / "talk.mp4").open("rb") as handle:
        asset = client.post(f"/api/v2/projects/{pid}/assets", files={"file": ("talk.mp4", handle)}).json()[
            "id"
        ]

    assert client.get(f"/api/v2/projects/{pid}/transcripts/{asset}").json()["state"] == "none"
    r = client.post(f"/api/v2/projects/{pid}/transcripts/{asset}", json={})
    assert r.status_code == 200, r.text
    data = _wait_done(client, pid, asset)
    assert data["state"] == "done", data
    assert FakeASR.calls == ["zh-CN"]
    assert data["fillers"] == [1]
    assert len(data["words"]) == len(WORDS)

    r = client.post(
        f"/api/v2/projects/{pid}/cut",
        json={
            "asset_id": asset,
            "removed": [1],
            "max_pause": 1.0,
            "split_scenes": False,
            "texts": {"6": "很保暖。"},
        },
    )
    assert r.status_code == 200, r.text
    project = r.json()
    shots = project["shots"]
    # 删掉的"嗯"和两句之间的长停顿都在同一个镜头里跳过，不会因此多出镜头。
    assert len(shots) == 1
    shot = shots[0]
    assert shot["speech"] == "original" and shot["visual"]["source_volume"] == 1.0
    assert shot["text"] == "大家好，今天介绍羽绒服。轻薄很保暖。"
    assert shot["visual"]["trim_start"] == 0.0
    assert shot["visual"]["trim_end"] == pytest.approx(6.45, abs=0.01)
    # 剪点会挪到附近最安静的地方（这段测试音是持续的正弦波，挪动很小）。
    assert [tuple(x) for x in shot["visual"]["skips"]] == [
        pytest.approx((1.28, 1.72), abs=0.03),
        pytest.approx((3.45, 4.95), abs=0.03),
    ]
    assert shot["duration"] == pytest.approx(4.51, abs=0.05)
    # 改过的错字记回识别结果。
    assert client.get(f"/api/v2/projects/{pid}/transcripts/{asset}").json()["words"][6][0] == "很保暖。"

    # 渲染：原声镜头不调用配音服务，剪掉的片段跳过，字幕用识别的时间戳（扣掉前面剪掉的时间）。
    job = client.post(f"/api/v2/projects/{pid}/renders").json()
    status = client.get(f"/api/v2/renders/{job['id']}").json()
    assert status["state"] == "done", status
    result = status["result"]
    assert result["duration"] == pytest.approx(4.51, abs=0.15)
    words = {w[0]: w for w in result["timings"][0]["words"]}
    assert "嗯，" not in words
    assert words["今天"][1] == pytest.approx(1.8 - 0.44, abs=0.02)
    assert words["轻薄"][1] == pytest.approx(5.2 - 0.44 - 1.5, abs=0.02)

    # 在镜头的第 2 秒拆开：换算成素材时间，剪掉的片段和字幕文字跟着分到两段。
    r = client.post(f"/api/v2/projects/{pid}/shots/{shot['id']}/split", json={"at": 2.0})
    assert r.status_code in (200, 201), r.text
    first, second = r.json()["shots"]
    assert first["visual"]["trim_end"] == pytest.approx(2.44, abs=0.03)
    assert [tuple(x) for x in first["visual"]["skips"]] == [pytest.approx((1.28, 1.72), abs=0.03)]
    assert first["text"] == "大家好，今天介绍" and second["text"] == "羽绒服。轻薄很保暖。"
    # 再合并回去：中间没有空隙，剪掉的片段保留。
    r = client.post(f"/api/v2/projects/{pid}/shots/{first['id']}/merge")
    merged = client.get(f"/api/v2/projects/{pid}").json()["shots"]
    assert len(merged) == 1 and len(merged[0]["visual"]["skips"]) == 2


def test_merge_shots_with_gap_records_skip():
    from shotwright.core.models import Asset, Project, Shot, ShotVisual
    from shotwright.services import editing

    asset = Asset(id="src", kind="video", source="upload", sha256="x", filename="a.mp4", duration=20.0)
    a = Shot(
        text="一",
        duration=2.0,
        visual=ShotVisual(
            mode="upload", selected="src", candidates=["src"], edit_asset="src", trim_start=0.0, trim_end=2.0
        ),
    )
    b = Shot(
        text="二",
        duration=3.0,
        visual=ShotVisual(
            mode="upload", selected="src", candidates=["src"], edit_asset="src", trim_start=5.0, trim_end=8.0
        ),
    )
    project = Project(shots=[a, b], assets={"src": asset})
    editing.merge_with_next(project, a.id)
    visual = project.shots[0].visual
    assert (visual.trim_start, visual.trim_end, visual.skips) == (0.0, 8.0, [(2.0, 5.0)])
    assert visual.kept_seconds(0.0, 8.0) == 5.0
    assert visual.to_source_time(0.0, 8.0, 2.5) == 5.5
    assert visual.to_shot_time(0.0, 8.0, 3.0) is None and visual.to_shot_time(0.0, 8.0, 6.0) == 3.0


def test_cut_requires_finished_transcript(client, tmp_path):
    pid = client.post("/api/v2/projects", json={"mode": "empty", "voice": {"provider": "silent"}}).json()[
        "id"
    ]
    with _clip_with_audio(tmp_path / "talk.mp4").open("rb") as handle:
        asset = client.post(f"/api/v2/projects/{pid}/assets", files={"file": ("talk.mp4", handle)}).json()[
            "id"
        ]
    r = client.post(f"/api/v2/projects/{pid}/cut", json={"asset_id": asset, "removed": []})
    assert r.status_code >= 400
    assert "识别" in r.json()["detail"]


def test_language_version_switches_original_speech_to_voiceover(store, settings, tmp_path):
    from shotwright.core.models import Project, Shot, ShotVisual
    from shotwright.services import localize

    project = Project(
        shots=[Shot(text="大家好", duration=2.0, speech="original", visual=ShotVisual(source_volume=1.0))]
    )

    class LLM:
        def complete(self, messages, *, json_mode=False):
            import json

            shots = [{"id": project.shots[0].id, "text": "Hello"}]
            return json.dumps({"title": "Hi", "shots": shots, "overlays": []})

    store.create(project)
    version = localize.create_language_version(store, LLM(), project, "en-US")
    shot = version.shots[0]
    assert shot.speech == "tts" and shot.visual.source_volume == 0.0 and shot.duration is None
    assert transcribe.shot_words(store, version, shot) == []


def test_auto_language_adopts_detected_language(client, settings, store, tmp_path):
    settings.defaults["asr"] = "fakeasr"
    project = client.post(
        "/api/v2/projects", json={"mode": "empty", "language": "en-US", "voice": {"provider": "silent"}}
    ).json()
    pid = project["id"]
    with _clip_with_audio(tmp_path / "talk.mp4").open("rb") as handle:
        asset = client.post(f"/api/v2/projects/{pid}/assets", files={"file": ("talk.mp4", handle)}).json()[
            "id"
        ]
    client.post(f"/api/v2/projects/{pid}/transcripts/{asset}", json={"language": "auto"})
    assert _wait_done(client, pid, asset)["state"] == "done"
    # 假识别报告的是中文：项目语言跟着改成简体中文（字幕字体、翻译版本按它来）。
    assert store.load(pid).language == "zh-CN"


def test_chosen_language_that_does_not_match_speech_is_corrected(client, settings, store, tmp_path):
    """选了英语、实际说中文（反过来也一样）：按实际语言识别后，项目语言跟着改，并记下当初选的语言。"""
    settings.defaults["asr"] = "fakeasr"
    project = client.post(
        "/api/v2/projects", json={"mode": "empty", "language": "en-US", "voice": {"provider": "silent"}}
    ).json()
    pid = project["id"]
    with _clip_with_audio(tmp_path / "talk.mp4").open("rb") as handle:
        asset = client.post(f"/api/v2/projects/{pid}/assets", files={"file": ("talk.mp4", handle)}).json()[
            "id"
        ]
    client.post(f"/api/v2/projects/{pid}/transcripts/{asset}", json={"language": "en-US"})
    data = _wait_done(client, pid, asset)
    assert data["state"] == "done" and data["requested_language"] == "en-US" and data["language"] == "zh"
    assert store.load(pid).language == "zh-CN"


def test_cut_points_snap_to_nearby_silence():
    """识别时间有误差：剪点挪到附近最安静的地方，不切字头、不留半个尾音。"""
    from array import array

    from shotwright.media import silence

    rate = silence.SAMPLE_RATE
    # 0–1.00 秒说话，1.00–1.30 秒静音，1.30–3.00 秒说话。
    samples = array("h", [8000 if (i // 4) % 2 else -8000 for i in range(3 * rate)])
    for i in range(int(1.0 * rate), int(1.3 * rate)):
        samples[i] = 0
    # 识别说第一段到 1.08 秒才结束、第二段 1.22 秒才开始：都落在静音里，基本不动。
    assert 1.0 <= silence.quietest(samples, 1.08) <= 1.3
    # 识别把剪点放在 0.92 秒（还在说话），挪进后面的静音里。
    assert 1.0 <= silence.quietest(samples, 0.92) <= 1.1
    # 剪点放在 1.40 秒（第二段已经开口），挪回前面的静音里。
    assert 1.2 <= silence.quietest(samples, 1.40) <= 1.3
    # 前后都在说话、附近没有静音：差不多的情况下留在原处附近。
    assert abs(silence.quietest(samples, 2.5) - 2.5) <= 0.02
    # 片头片尾不动，段落先后不乱。
    ranges = silence.snap_ranges(samples, [(0.0, 0.92), (1.40, 3.0)], 3.0)
    assert ranges[0][0] == 0.0 and ranges[-1][1] == 3.0
    assert 1.0 <= ranges[0][1] <= ranges[1][0] <= 1.3


def test_whisper_checks_spoken_language_before_transcribing():
    from shotwright.providers.asr import faster_whisper as fw

    class Model:
        def __init__(self, detected, probability):
            self.result = (detected, probability, [])

        def detect_language(self, samples, **kwargs):
            return self.result

    # 很有把握是英语：选了中文也按英语识别，不再把英语写成谐音中文。
    assert fw._check_language(Model("en", 0.95), [], "zh") == "en"
    # 没把握时尊重用户选的语言。
    assert fw._check_language(Model("en", 0.4), [], "zh") == "zh"
    # 自动识别：先定下语言（中文才能用上简体加标点的提示）。
    assert fw._check_language(Model("zh", 0.6), [], None) == "zh"
    # 旧版没有 detect_language：按选的语言识别。
    assert fw._check_language(object(), [], "zh") == "zh"


def test_faster_whisper_download_falls_back_to_mirror(monkeypatch, tmp_path):
    """连不上 Hugging Face 时换 hf-mirror.com 下载，而不是一直卡住；都连不上时给出明确的报错。"""
    pytest.importorskip("faster_whisper")
    import huggingface_hub

    from shotwright.providers.asr import faster_whisper as fw
    from shotwright.providers.base import ProviderError

    calls = []

    def fake_snapshot(repo, *, local_files_only=False, endpoint=None, **kwargs):
        calls.append((repo, local_files_only, endpoint))
        if local_files_only:
            raise FileNotFoundError("not cached")
        return str(tmp_path / "model")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
    monkeypatch.setattr(fw, "_reachable", lambda url, proxy=None: "hf-mirror.com" in url)
    stages = []
    asr = fw.FasterWhisperASR({"model": "small", "download_root": str(tmp_path)})
    assert asr._download(lambda value, stage: stages.append(stage)) == str(tmp_path / "model")
    assert calls == [
        ("Systran/faster-whisper-small", True, None),
        ("Systran/faster-whisper-small", False, "https://hf-mirror.com"),
    ]

    monkeypatch.setattr(fw, "_reachable", lambda url, proxy=None: False)
    with pytest.raises(ProviderError, match="下载失败"):
        asr._download(None)


def test_faster_whisper_gets_decoded_samples_not_a_path(monkeypatch, tmp_path):
    """faster-whisper 自带的解码依赖 PyAV，新版 PyAV 不兼容；改为 ffmpeg 解码后把采样数组交给它。"""
    pytest.importorskip("faster_whisper")
    from types import SimpleNamespace

    import numpy as np

    from shotwright.providers.asr import faster_whisper as fw

    audio = tmp_path / "speech.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=300:duration=2",
            str(audio),
        ],
        check=True,
    )
    seen = {}

    class Model:
        def transcribe(self, samples, **kwargs):
            seen["samples"] = samples
            word = SimpleNamespace(word=" 你好", start=0.1, end=0.5)
            segment = SimpleNamespace(start=0.0, end=1.0, words=[word])
            return iter([segment]), SimpleNamespace(duration=2.0, language="zh")

    asr = fw.FasterWhisperASR({})
    monkeypatch.setattr(asr, "_model", lambda progress=None: Model())
    result = asr.transcribe(audio, language="zh-CN")
    samples = seen["samples"]
    assert isinstance(samples, np.ndarray) and samples.dtype == np.float32
    assert abs(len(samples) - 2 * fw.SAMPLE_RATE) < fw.SAMPLE_RATE * 0.1
    assert result.words[0].text == "你好" and result.language == "zh"


def test_cut_projects_are_listed_separately(client):
    created = client.post("/api/v2/projects", json={"mode": "empty"}).json()
    cut = client.post("/api/v2/projects", json={"mode": "empty", "origin": "cut"}).json()
    assert cut["origin"] == "cut" and created["origin"] == "create"
    listed = {p["id"]: p["origin"] for p in client.get("/api/v2/projects").json()}
    assert listed[cut["id"]] == "cut" and listed[created["id"]] == "create"


def test_old_cut_task_without_origin_is_listed_under_cut(client, settings, store, tmp_path):
    """旧版剪辑页建的任务没有 origin 字段：识别过原片、还没生成镜头的也算剪辑任务。"""
    settings.defaults["asr"] = "fakeasr"
    pid = client.post("/api/v2/projects", json={"mode": "empty"}).json()["id"]
    with _clip_with_audio(tmp_path / "talk.mp4").open("rb") as handle:
        asset = client.post(f"/api/v2/projects/{pid}/assets", files={"file": ("talk.mp4", handle)}).json()[
            "id"
        ]
    client.post(f"/api/v2/projects/{pid}/transcripts/{asset}", json={})
    _wait_done(client, pid, asset)
    listed = {p["id"]: p for p in client.get("/api/v2/projects").json()}
    assert listed[pid]["origin"] == "cut" and listed[pid]["source_asset"] == asset


def test_cut_draft_is_saved_on_server(client, settings, tmp_path):
    """剪辑进度存在服务端：换浏览器打开还在；生成分镜时用的设置也记下来。"""
    settings.defaults["asr"] = "fakeasr"
    pid = client.post("/api/v2/projects", json={"mode": "empty"}).json()["id"]
    with _clip_with_audio(tmp_path / "talk.mp4").open("rb") as handle:
        asset = client.post(f"/api/v2/projects/{pid}/assets", files={"file": ("talk.mp4", handle)}).json()[
            "id"
        ]
    url = f"/api/v2/projects/{pid}/transcripts/{asset}"
    # 识别完成前不能保存
    assert client.put(f"{url}/draft", json={"removed": [1]}).status_code == 409
    client.post(url, json={})
    _wait_done(client, pid, asset)
    r = client.put(
        f"{url}/draft",
        json={"removed": [1, 99, 1], "texts": {"6": "很保暖。", "x": "?"}, "pauses": True, "max_pause": 1.2},
    )
    assert r.status_code == 200, r.text
    draft = client.get(url).json()["draft"]
    assert draft["removed"] == [1] and draft["texts"] == {"6": "很保暖。"} and draft["max_pause"] == 1.2
    client.post(f"/api/v2/projects/{pid}/cut", json={"asset_id": asset, "removed": [0, 1], "max_pause": None})
    draft = client.get(url).json()["draft"]
    assert draft["removed"] == [0, 1] and draft["pauses"] is False
