"""示例项目：一键建好、画面齐全、不用 Key 就能渲染。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from shotwright.services import sample


def test_paint_every_scene():
    for scene in sample.PAINTERS:
        image = sample.paint(scene, (108, 192))
        assert image.size == (108, 192)
        assert image.mode == "RGB"


def test_sample_texts_match_in_both_languages():
    assert len(sample.SHOTS["zh"]) == len(sample.SHOTS["en"])
    assert {item.scene for item in sample.SHOTS["zh"]} == set(sample.PAINTERS)


def test_create_sample_project(client: TestClient):
    resp = client.post("/api/v2/projects/sample?language=zh-CN")
    assert resp.status_code == 201, resp.text
    project = resp.json()
    assert project["title"].startswith("示例")
    assert len(project["shots"]) == 5
    for shot in project["shots"]:
        visual = shot["visual"]
        assert visual["mode"] == "upload"
        assert visual["selected"] in project["assets"]
        assert project["assets"][visual["selected"]]["kind"] == "image"
    assert project["look"]["confirmed"] is True
    assert project["titles"]["headline"]["enabled"] is True
    assert [o["text"] for o in project["overlays"]] == ["第二瓶半价", "点下方链接下单"]
    # 体检里不该有「缺画面」「没有描述」这类问题。
    issues = client.get(f"/api/v2/projects/{project['id']}/checkup").json()["issues"]
    assert not [i for i in issues if i["code"] in ("no_visual", "no_prompt")]


def test_create_sample_project_english(client: TestClient):
    project = client.post("/api/v2/projects/sample?language=en-US").json()
    assert project["language"] == "en-US"
    assert project["title"].startswith("Sample")


def test_sample_project_renders_without_keys(client: TestClient):
    """示例项目不配任何 Key 也能直接渲染出成片（测试里配音用静音）。"""
    pid = client.post("/api/v2/projects/sample").json()["id"]
    job = client.post(f"/api/v2/projects/{pid}/renders", json={"quality": "draft"}).json()
    status = client.get(f"/api/v2/renders/{job['id']}").json()
    assert status["state"] == "done", status
