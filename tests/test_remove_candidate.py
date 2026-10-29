"""从镜头的候选素材里删除素材。"""

from shotwright.core.models import Asset, Project, Shot, ShotVisual, Take
from shotwright.services import visuals


def _project() -> tuple[Project, Shot]:
    assets = {
        name: Asset(
            id=name, kind="video", source="upload", filename=f"{name}.mp4", sha256=name * 4, duration=5.0
        )
        for name in ("aaa", "bbb", "ccc")
    }
    shot = Shot(
        text="一句话",
        visual=ShotVisual(
            mode="upload",
            candidates=["aaa", "bbb", "ccc"],
            selected="bbb",
            sequence=["ccc"],
            edit_asset="bbb",
            takes=[Take(asset_id="bbb")],
        ),
    )
    return Project(shots=[shot], assets=assets), shot


def test_removing_the_selected_clip_promotes_the_next_in_sequence():
    project, shot = _project()
    visuals.remove_candidate(project, shot, "bbb")
    v = shot.visual
    assert v.candidates == ["aaa", "ccc"]
    assert (v.selected, v.sequence, v.edit_asset, v.takes) == ("ccc", [], None, [])
    assert "bbb" in project.assets  # 文件仍留在项目里


def test_removing_the_selected_clip_falls_back_to_the_latest_candidate():
    project, shot = _project()
    shot.visual.sequence = []
    visuals.remove_candidate(project, shot, "bbb")
    assert shot.visual.selected == "ccc"
    visuals.remove_candidate(project, shot, "ccc")
    assert shot.visual.selected == "aaa"
    visuals.remove_candidate(project, shot, "aaa")
    assert (shot.visual.selected, shot.visual.candidates) == (None, [])


def test_removing_a_clip_in_the_sequence_keeps_the_selection():
    project, shot = _project()
    visuals.remove_candidate(project, shot, "ccc")
    assert (shot.visual.selected, shot.visual.sequence) == ("bbb", [])


def test_remove_candidate_api(client, tmp_path):
    created = client.post(
        "/api/v2/projects", json={"mode": "split", "script": "一句话。", "voice": {"provider": "silent"}}
    ).json()
    pid, sid = created["id"], created["shots"][0]["id"]
    from PIL import Image

    paths = []
    for color in ("red", "blue"):
        path = tmp_path / f"{color}.png"
        Image.new("RGB", (64, 64), color).save(path)
        paths.append(path)
    for path in paths:
        with path.open("rb") as handle:
            response = client.post(
                f"/api/v2/projects/{pid}/assets", files={"file": (path.name, handle, "image/png")}
            )
        assert response.status_code == 201, response.text
        visual = {"mode": "upload", "selected": response.json()["id"]}
        client.patch(f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": visual})
    shot = client.get(f"/api/v2/projects/{pid}").json()["shots"][0]
    first, second = shot["visual"]["candidates"][-2:]
    assert shot["visual"]["selected"] == second
    result = client.delete(f"/api/v2/projects/{pid}/shots/{sid}/candidates/{second}").json()
    visual = result["shots"][0]["visual"]
    assert second not in visual["candidates"] and visual["selected"] == first
    assert client.delete(f"/api/v2/projects/{pid}/shots/{sid}/candidates/{second}").status_code == 404
    client.patch(f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"locked": True}})
    assert client.delete(f"/api/v2/projects/{pid}/shots/{sid}/candidates/{first}").status_code == 409
