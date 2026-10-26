"""分镜头多选素材：一个镜头里依次播放几段素材。"""

from conftest import make_clip, make_image

from shotwright.core.models import Project, Shot, ShotVisual, VoiceSettings
from shotwright.media.ffmpeg import probe
from shotwright.pipeline.render import Renderer
from shotwright.pipeline.shots import shot_assets, split_duration


def _project(store, tmp_path):
    text = "这一个镜头的旁白稍微长一些，用来放好几段素材依次播放。"
    project = store.create(
        Project(
            voice=VoiceSettings(provider="silent"), shots=[Shot(text=text, visual=ShotVisual(mode="upload"))]
        )
    )
    a = store.add_asset(
        project, make_clip(tmp_path / "a.mp4", seconds=2.0), kind="video", source="upload", duration=2.0
    )
    b = store.add_asset(
        project,
        make_clip(tmp_path / "b.mp4", seconds=4.0, pattern="smptebars"),
        kind="video",
        source="upload",
        duration=4.0,
    )
    c = store.add_asset(project, make_image(tmp_path / "c.png"), kind="image", source="upload")
    store.save(project)
    return project, a, b, c


def test_split_duration_follows_usable_length():
    assert split_duration([2.0, 4.0], 3.0) == [1.0, 2.0]
    shares = split_duration([2.0, 4.0, 3.0], 10.0)
    assert abs(sum(shares) - 10.0) < 1e-6 and shares[1] > shares[0]


def test_shot_plays_selected_assets_in_order(store, settings, tmp_path):
    project, a, b, c = _project(store, tmp_path)
    shot = project.shots[0]
    shot.visual.selected = a.id
    shot.visual.sequence = [b.id, c.id, a.id]  # 重复的会被去掉
    shot.visual.candidates = [a.id, b.id, c.id]
    store.save(project)
    assert [x.id for x in shot_assets(project, shot)] == [a.id, b.id, c.id]

    result = Renderer(store, settings).render(project.id)
    info = probe(store.render_dir(project.id, result.render_id) / result.video)
    assert abs(info.duration - result.timings[0].duration) < 0.3

    # 不改动时复用缓存；改多选顺序时重做这个镜头。
    again = Renderer(store, settings).render(project.id)
    assert {s.key for s in again.stages if not s.reused} == set()
    project = store.load(project.id)
    project.shots[0].visual.sequence = [c.id, b.id]
    store.save(project)
    changed = Renderer(store, settings).render(project.id)
    assert f"{project.shots[0].id}:shot" in {s.key for s in changed.stages if not s.reused}


def test_api_multiselect_normalizes_and_clears_on_mode_switch(client, store, tmp_path):
    project, a, b, c = _project(store, tmp_path)
    shot_id = project.shots[0].id
    url = f"/api/v2/projects/{project.id}/shots/{shot_id}"
    data = client.patch(url, json={"visual": {"selected": a.id, "sequence": [a.id, b.id, b.id, c.id]}}).json()
    visual = data["shots"][0]["visual"] if "shots" in data else data["visual"]
    assert visual["sequence"] == [b.id, c.id]
    assert set(visual["candidates"]) >= {a.id, b.id, c.id}

    # 选中多选里的某一段：它变成第一段，不再重复出现在多选列表里。
    data = client.patch(url, json={"visual": {"selected": b.id}}).json()
    visual = data["shots"][0]["visual"]
    assert visual["selected"] == b.id and visual["sequence"] == [c.id]

    assert client.patch(url, json={"visual": {"sequence": ["missing"]}}).status_code == 422
    data = client.patch(url, json={"visual": {"mode": "stock"}}).json()
    assert data["shots"][0]["visual"]["sequence"] == []
