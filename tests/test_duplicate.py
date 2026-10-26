"""复制项目：副本独立可编辑，原项目不受影响。"""

from conftest import make_clip

from shotwright.core.models import PendingTask, Project, Shot, ShotVisual, VoiceSettings
from shotwright.pipeline.render import Renderer
from shotwright.services.duplicate import duplicate_project


def test_duplicate_copies_assets_and_cache_but_not_history(store, settings, tmp_path):
    shots = [
        Shot(text="第一句。", visual=ShotVisual(mode="upload")),
        Shot(text="第二句。", visual=ShotVisual(mode="color")),
    ]
    project = store.create(
        Project(
            title="篮球联赛",
            voice=VoiceSettings(provider="silent"),
            shots=shots,
            group="g1",
            cost_credits=3.5,
        )
    )
    clip = store.add_asset(project, make_clip(tmp_path / "a.mp4", seconds=1.0), kind="video", source="upload")
    project.shots[0].visual.selected = clip.id
    project.shots[0].visual.candidates = [clip.id]
    store.save(project)
    Renderer(store, settings).render(project.id)
    project = store.load(project.id)
    project.shots[1].visual.pending = PendingTask(provider="seedance", task_id="t1")
    store.save(project)

    copy = duplicate_project(store, project)
    assert copy.id != project.id and copy.title == "篮球联赛（副本）"
    assert copy.group is None and copy.cost_credits == 0 and copy.shots[1].visual.pending is None
    assert store.asset_path(copy, clip.id).is_file()
    assert not (store.project_dir(copy.id) / "renders").exists()  # 历史成片不复制

    # 渲染缓存一起复制：副本第一次渲染时没改动的镜头直接复用。
    result = Renderer(store, settings).render(copy.id)
    assert all(stage.reused for stage in result.stages if stage.kind in ("voice", "shot"))

    # 改副本不影响原项目。
    copy = store.load(copy.id)
    copy.shots[0].text = "改过的第一句。"
    store.save(copy)
    assert store.load(project.id).shots[0].text == "第一句。"


def test_duplicate_api(client):
    created = client.post(
        "/api/v2/projects",
        json={"mode": "split", "script": "一句话。", "title": "原片", "voice": {"provider": "silent"}},
    ).json()
    response = client.post(f"/api/v2/projects/{created['id']}/duplicate", json={"title": "新任务"})
    assert response.status_code == 201
    copy = response.json()
    assert copy["id"] != created["id"] and copy["title"] == "新任务"
    assert [s["text"] for s in copy["shots"]] == [s["text"] for s in created["shots"]]
    assert client.post("/api/v2/projects/missing/duplicate").status_code == 404
    titles = {p["title"] for p in client.get("/api/v2/projects").json()}
    assert {"原片", "新任务"} <= titles
