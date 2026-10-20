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
