"""一键生成和审片标记。"""

import time

import pytest
from conftest import FakeImageGen

from shotwright.services import batch as batch_service
from shotwright.services import cost


@pytest.fixture(autouse=True)
def _clean():
    batch_service.clear()
    yield
    batch_service.clear()


def _wait(client, pid):
    for _ in range(200):
        job = client.get(f"/api/v2/projects/{pid}/generate-all").json()["job"]
        if job and job["state"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("一键生成没有结束")


def _project(client):
    created = client.post(
        "/api/v2/projects",
        json={"mode": "split", "script": "第一句。第二句。第三句。", "voice": {"provider": "silent"}},
    ).json()
    pid = created["id"]
    for text in ("第二个镜头", "第三个镜头"):
        client.post(f"/api/v2/projects/{pid}/shots", json={"text": text})
    ids = [shot["id"] for shot in client.get(f"/api/v2/projects/{pid}").json()["shots"]][:3]
    for sid in ids[:2]:
        client.patch(
            f"/api/v2/projects/{pid}/shots/{sid}",
            json={"visual": {"mode": "image", "image_prompt": "一盏台灯"}},
        )
    client.patch(f"/api/v2/projects/{pid}/shots/{ids[2]}", json={"visual": {"mode": "color"}})
    return pid, ids


def test_generate_all_fills_missing_ai_shots(client, monkeypatch):
    pid, ids = _project(client)
    plan = client.get(f"/api/v2/projects/{pid}/generate-all").json()["plan"]
    assert [item["shot_id"] for item in plan["items"]] == ids[:2]
    assert all(item["reason"] == "missing" for item in plan["items"])

    monkeypatch.setattr(cost, "visual_generation_cost", lambda *a, **k: (0.5, "fake"))
    assert client.get(f"/api/v2/projects/{pid}/generate-all").json()["plan"]["total"] == 1.0
    refused = client.post(f"/api/v2/projects/{pid}/generate-all", json={})
    assert refused.status_code == 402
    client.patch(f"/api/v2/projects/{pid}", json={"budget": 0.6})
    assert (
        client.post(f"/api/v2/projects/{pid}/generate-all", json={"confirm_charge": True}).status_code == 409
    )
    client.patch(f"/api/v2/projects/{pid}", json={"budget": None})

    started = client.post(f"/api/v2/projects/{pid}/generate-all", json={"confirm_charge": True})
    assert started.status_code == 200 and started.json()["total"] == 2
    job = _wait(client, pid)
    assert job["state"] == "done" and job["done"] == ids[:2] and not job["failed"]
    project = client.get(f"/api/v2/projects/{pid}").json()
    for shot in project["shots"][:2]:
        assert shot["visual"]["selected"] in project["assets"]
    assert client.get(f"/api/v2/projects/{pid}/generate-all").json()["plan"]["items"] == []
    assert (
        client.post(f"/api/v2/projects/{pid}/generate-all", json={"confirm_charge": True}).status_code == 409
    )


def test_review_marks_and_redo(client):
    pid, ids = _project(client)
    assert client.post(f"/api/v2/projects/{pid}/generate-all", json={}).status_code == 402
    client.post(f"/api/v2/projects/{pid}/generate-all", json={"confirm_charge": True})
    _wait(client, pid)
    before = client.get(f"/api/v2/projects/{pid}").json()["shots"][0]
    first = before["visual"]["selected"]

    marked = client.patch(
        f"/api/v2/projects/{pid}/shots/{ids[0]}",
        json={"visual": {"review": "redo", "review_note": "  灯光再暖一点 "}},
    ).json()["shots"][0]
    assert marked["visual"]["review"] == "redo" and marked["visual"]["review_note"] == "灯光再暖一点"
    assert marked["status"] == before["status"]
    client.patch(f"/api/v2/projects/{pid}/shots/{ids[1]}", json={"visual": {"review": "ok"}})

    plan = client.get(f"/api/v2/projects/{pid}/generate-all?only_redo=true").json()["plan"]
    assert [(item["shot_id"], item["reason"], item["review_note"]) for item in plan["items"]] == [
        (ids[0], "redo", "灯光再暖一点")
    ]
    calls = len(FakeImageGen.calls)
    client.post(f"/api/v2/projects/{pid}/generate-all", json={"only_redo": True, "confirm_charge": True})
    job = _wait(client, pid)
    assert job["done"] == [ids[0]]
    shots = client.get(f"/api/v2/projects/{pid}").json()["shots"]
    assert shots[0]["visual"]["review"] == "" and shots[0]["visual"]["review_note"] == ""
    assert len(FakeImageGen.calls) == calls + 1
    assert first in shots[0]["visual"]["candidates"]
    assert shots[1]["visual"]["review"] == "ok"


def test_locked_and_cancelled(client):
    pid, ids = _project(client)
    client.patch(f"/api/v2/projects/{pid}/shots/{ids[0]}", json={"visual": {"locked": True}})
    plan = client.get(f"/api/v2/projects/{pid}/generate-all").json()["plan"]
    assert [item["shot_id"] for item in plan["items"]] == [ids[1]]
    assert client.post(f"/api/v2/projects/{pid}/generate-all/cancel").status_code == 404

    job = batch_service.start(pid, [ids[1]], "final", 0)
    batch_service.cancel(pid)
    assert job.cancel_requested
    batch_service.finish(job)
    assert job.state == "cancelled"
