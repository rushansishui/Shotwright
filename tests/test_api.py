from concurrent.futures import Executor, Future

import pytest
from fastapi.testclient import TestClient

from shotwright.api.app import create_app
from shotwright.api.jobs import JobManager
from shotwright.core.store import ProjectStore
from shotwright.media.ffmpeg import run_ffmpeg


class InlineExecutor(Executor):
    """测试里同步执行渲染任务。"""

    def submit(self, fn, *args, **kwargs):
        future: Future = Future()
        future.set_result(fn(*args, **kwargs))
        return future


@pytest.fixture
def client(settings):
    store = ProjectStore(settings.storage_dir)
    app = create_app(settings, job_manager=JobManager(store, settings, executor=InlineExecutor()))
    with TestClient(app) as test_client:
        yield test_client


def _create(client, script="第一句话。第二句话说得长一些，让它单独成为一个镜头好不好呢。第三句。"):
    response = client.post(
        "/api/v2/projects", json={"mode": "split", "script": script, "voice": {"provider": "silent"}}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_health_and_providers(client):
    assert client.get("/api/v2/health").json()["status"] == "ok"
    providers = client.get("/api/v2/providers").json()
    assert {p["id"] for p in providers["tts"]} >= {"edge", "silent"}
    pexels = next(p for p in providers["stock"] if p["id"] == "pexels")
    assert pexels["configured"] is False
    assert all("value" not in f for f in pexels["fields"])


def test_create_edit_and_reorder(client):
    project = _create(client)
    pid = project["id"]
    shots = project["shots"]
    assert len(shots) >= 2

    r = client.patch(
        f"/api/v2/projects/{pid}/shots/{shots[0]['id']}",
        json={"text": "改过的开头。", "visual": {"terms": [" city ", ""], "locked": True}},
    )
    assert r.status_code == 200, r.text
    first = r.json()["shots"][0]
    assert first["text"] == "改过的开头。"
    assert first["visual"]["terms"] == ["city"]
    assert first["visual"]["locked"] is True

    order = [s["id"] for s in shots][::-1]
    r = client.put(f"/api/v2/projects/{pid}/shots/order", json={"order": order})
    assert [s["id"] for s in r.json()["shots"]] == order

    r = client.put(f"/api/v2/projects/{pid}/shots/order", json={"order": order[:1]})
    assert r.status_code == 422

    r = client.post(f"/api/v2/projects/{pid}/shots", json={"text": "新镜头", "after": order[0]})
    assert r.json()["shots"][1]["text"] == "新镜头"

    r = client.delete(f"/api/v2/projects/{pid}/shots/{order[0]}")
    assert order[0] not in [s["id"] for s in r.json()["shots"]]


def test_rejects_bad_ids_and_unknown_assets(client):
    pid = _create(client)["id"]
    assert client.get("/api/v2/projects/..%2F..%2Fetc").status_code in (400, 404)
    shot = client.get(f"/api/v2/projects/{pid}").json()["shots"][0]["id"]
    r = client.patch(f"/api/v2/projects/{pid}/shots/{shot}", json={"visual": {"selected": "nope"}})
    assert r.status_code == 422
    r = client.patch(f"/api/v2/projects/{pid}", json={"bgm": {"asset_id": "nope"}})
    assert r.status_code == 422


def test_llm_mode_without_config_reports_error(client):
    r = client.post("/api/v2/projects", json={"mode": "llm", "subject": "AI"})
    assert r.status_code == 502
    assert "Provider" in r.json()["detail"] or "配置" in r.json()["detail"]


def test_upload_candidates_and_render(client, tmp_path):
    pid = _create(client)["id"]
    shot = client.get(f"/api/v2/projects/{pid}").json()["shots"][0]["id"]

    image = tmp_path / "a.png"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=320x320", "-frames:v", "1", str(image)])
    with image.open("rb") as handle:
        r = client.post(f"/api/v2/projects/{pid}/assets", files={"file": ("a.png", handle, "image/png")})
    assert r.status_code == 201, r.text
    asset = r.json()
    assert asset["kind"] == "image"
    assert client.get(f"/api/v2/projects/{pid}/assets/{asset['id']}").status_code == 200

    r = client.patch(
        f"/api/v2/projects/{pid}/shots/{shot}", json={"visual": {"mode": "upload", "selected": asset["id"]}}
    )
    assert r.json()["shots"][0]["visual"]["selected"] == asset["id"]

    second = client.get(f"/api/v2/projects/{pid}").json()["shots"][1]["id"]
    client.patch(f"/api/v2/projects/{pid}/shots/{second}", json={"visual": {"terms": ["ocean"]}})
    r = client.post(f"/api/v2/projects/{pid}/shots/{second}/candidates")
    assert r.status_code == 200, r.text
    visual = r.json()["shots"][1]["visual"]
    assert len(visual["candidates"]) == 2 and visual["selected"] == visual["candidates"][0]

    job = client.post(f"/api/v2/projects/{pid}/renders").json()
    status = client.get(f"/api/v2/renders/{job['id']}").json()
    assert status["state"] == "done", status
    video = client.get(f"/api/v2/projects/{pid}/renders/{job['id']}/video.mp4")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    listing = client.get(f"/api/v2/projects/{pid}/renders").json()
    assert listing["renders"][0]["render_id"] == job["id"]
    assert client.get(f"/api/v2/projects/{pid}/renders/{job['id']}/project.json").status_code == 404


def test_locked_shot_refuses_new_candidates(client):
    pid = _create(client)["id"]
    shot = client.get(f"/api/v2/projects/{pid}").json()["shots"][0]["id"]
    client.patch(f"/api/v2/projects/{pid}/shots/{shot}", json={"visual": {"locked": True, "terms": ["x"]}})
    assert client.post(f"/api/v2/projects/{pid}/shots/{shot}/candidates").status_code == 409


def test_failed_render_reports_reason(client):
    r = client.post("/api/v2/projects", json={"mode": "empty"})
    pid = r.json()["id"]
    job = client.post(f"/api/v2/projects/{pid}/renders").json()
    status = client.get(f"/api/v2/renders/{job['id']}").json()
    assert status["state"] == "failed"
    assert "镜头" in status["error"]


def test_paid_generation_needs_confirm_charge(client):
    pid = _create(client)["id"]
    shot = client.get(f"/api/v2/projects/{pid}").json()["shots"][0]["id"]
    r = client.patch(
        f"/api/v2/projects/{pid}/shots/{shot}", json={"visual": {"mode": "image", "prompt": "猫"}}
    )
    assert r.status_code == 200
    r = client.post(f"/api/v2/projects/{pid}/shots/{shot}/candidates")
    assert r.status_code == 402 and r.json()["code"] == "confirm_charge"
    r = client.post(f"/api/v2/projects/{pid}/shots/{shot}/candidates", json={"confirm_charge": True})
    assert r.status_code == 200
    visual = r.json()["shots"][0]["visual"]
    assert visual["selected"] and r.json()["assets"][visual["selected"]]["kind"] == "image"

    job = client.post(f"/api/v2/projects/{pid}/renders", json={"confirm_charge": True}).json()
    assert job["allow_paid"] is True
    assert client.get(f"/api/v2/renders/{job['id']}").json()["state"] == "done"


def test_providers_lists_ai_kinds(client):
    providers = client.get("/api/v2/providers").json()
    assert any(p["id"] == "seedance" and p["paid"] for p in providers["video_gen"])
    assert any(p["id"] == "seedream" for p in providers["image_gen"])


def test_new_project_uses_configured_tts(client):
    project = _create(client)
    assert project["voice"]["provider"] == "silent"


def test_generation_in_progress_is_visible_and_not_resubmitted(client, monkeypatch):
    import threading

    from conftest import FakeImageGen

    started, release = threading.Event(), threading.Event()
    original = FakeImageGen.generate

    def slow(self, prompt, *, aspect, output):
        started.set()
        release.wait(10)
        return original(self, prompt, aspect=aspect, output=output)

    monkeypatch.setattr(FakeImageGen, "generate", slow)
    pid = _create(client)["id"]
    shot = client.get(f"/api/v2/projects/{pid}").json()["shots"][0]["id"]
    client.patch(f"/api/v2/projects/{pid}/shots/{shot}", json={"visual": {"mode": "image", "prompt": "猫"}})
    url = f"/api/v2/projects/{pid}/shots/{shot}/candidates"
    result = {}
    worker = threading.Thread(target=lambda: result.update(r=client.post(url, json={"confirm_charge": True})))
    worker.start()
    assert started.wait(10)
    progress = client.get(f"/api/v2/projects/{pid}/in-progress").json()
    assert progress[shot]["mode"] == "image"
    assert client.post(url, json={"confirm_charge": True}).status_code == 409
    release.set()
    worker.join(10)
    assert result["r"].status_code == 200
    assert client.get(f"/api/v2/projects/{pid}/in-progress").json() == {}


def test_first_frame_can_be_set_and_cleared(client):
    pid = _create(client)["id"]
    shot = client.get(f"/api/v2/projects/{pid}").json()["shots"][0]["id"]
    base = f"/api/v2/projects/{pid}/shots/{shot}"
    client.patch(base, json={"visual": {"mode": "image", "prompt": "猫"}})
    image = client.post(f"{base}/candidates", json={"confirm_charge": True}).json()["shots"][0]["visual"][
        "selected"
    ]
    visual = client.patch(base, json={"visual": {"mode": "ai"}}).json()["shots"][0]["visual"]
    assert visual["first_frame"] == image and visual["selected"] is None
    visual = client.patch(base, json={"visual": {"clear_first_frame": True}}).json()["shots"][0]["visual"]
    assert visual["first_frame"] is None
    assert client.patch(base, json={"visual": {"first_frame": image}}).json()["shots"][0]["visual"][
        "first_frame"
    ]


def test_languages_and_project_language(client):
    langs = client.get("/api/v2/languages").json()
    assert {"zh-CN", "en-US", "ja-JP", "ko-KR"} <= {item["code"] for item in langs}
    voices = client.get("/api/v2/voices", params={"language": "ja-JP"}).json()
    assert voices and all(v["id"].startswith("ja-JP") for v in voices)
    project = client.post(
        "/api/v2/projects",
        json={"mode": "split", "script": "Hello there. Nice to meet you.", "language": "en-US"},
    ).json()
    assert project["language"] == "en-US"
    patched = client.patch(f"/api/v2/projects/{project['id']}", json={"language": "ko-KR"}).json()
    assert patched["language"] == "ko-KR" and patched["subtitle"]["font"] == "NotoSansKR-Bold.ttf"
    assert client.patch(f"/api/v2/projects/{project['id']}", json={"language": "xx"}).status_code == 422
    versions = client.get(f"/api/v2/projects/{project['id']}/versions").json()
    assert [v["id"] for v in versions] == [project["id"]]


def test_delete_refuses_while_rendering(client):
    import threading

    from shotwright.api.jobs import Job

    pid = _create(client)["id"]
    jobs = client.app.state.jobs
    running = Job(id="busyjob", project_id=pid, state="running")
    with jobs._lock:
        jobs._jobs[running.id] = running
        jobs._cancel[running.id] = threading.Event()
    try:
        assert client.delete(f"/api/v2/projects/{pid}").status_code == 409
        # 强制删除会先发出取消信号（正在跑的渲染随后自行收尾为 cancelled），再删目录。
        assert client.delete(f"/api/v2/projects/{pid}", params={"force": "true"}).status_code == 204
        assert jobs._cancel["busyjob"].is_set()
    finally:
        with jobs._lock:
            jobs._jobs.pop(running.id, None)
            jobs._cancel.pop(running.id, None)


def test_subject_limit_is_enforced_by_api(client):
    from shotwright.core.models import MAX_SUBJECT_BYTES

    ok = client.post("/api/v2/projects", json={"mode": "empty", "subject": "a" * MAX_SUBJECT_BYTES})
    assert ok.status_code == 201
    too_long = client.post(
        "/api/v2/projects", json={"mode": "empty", "subject": "a" * (MAX_SUBJECT_BYTES + 1)}
    )
    assert too_long.status_code == 422
    # 中文按 UTF-8 字节算：1366 个汉字 = 4098 字节，超限。
    chinese = client.post("/api/v2/projects", json={"mode": "empty", "subject": "字" * 1366})
    assert chinese.status_code == 422
    assert client.post("/api/v2/projects", json={"mode": "empty", "subject": "字" * 1365}).status_code == 201


def test_subject_limit_is_enforced_by_model():
    import pytest

    from shotwright.core.models import MAX_SUBJECT_BYTES, Project

    assert len(Project(subject="a" * MAX_SUBJECT_BYTES).subject) == MAX_SUBJECT_BYTES
    with pytest.raises(ValueError, match="4096"):
        Project(subject="a" * (MAX_SUBJECT_BYTES + 1))


def test_patch_updates_target_seconds(client):
    pid = _create(client)["id"]
    r = client.patch(f"/api/v2/projects/{pid}", json={"target_seconds": 45})
    assert r.status_code == 200 and r.json()["target_seconds"] == 45
    assert client.patch(f"/api/v2/projects/{pid}", json={"target_seconds": 0}).status_code == 422


def test_create_project_saves_duration(client):
    project = client.post(
        "/api/v2/projects", json={"mode": "split", "script": "一。二。", "duration": 30}
    ).json()
    assert project["target_seconds"] == 30
