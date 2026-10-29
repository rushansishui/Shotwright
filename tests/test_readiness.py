"""先准备素材、确认之后才能生成 AI 画面。"""

import pytest
from conftest import make_image

pytestmark = pytest.mark.gate


def _project(client, tmp_path, *, appearance="立领无帽短款", labels=True):
    created = client.post(
        "/api/v2/projects", json={"mode": "split", "script": "一句话。", "voice": {"provider": "silent"}}
    ).json()
    pid, sid = created["id"], created["shots"][0]["id"]
    ids = []
    for index, size in enumerate(("320x320", "330x330")):
        with make_image(tmp_path / f"{index}.png", size=size).open("rb") as handle:
            ids.append(
                client.post(
                    f"/api/v2/projects/{pid}/assets", files={"file": (f"{index}.png", handle)}
                ).json()["id"]
            )
    look = {
        "subjects": [
            {
                "code": "P1",
                "name": "羽绒服",
                "kind": "product",
                "appearance": appearance,
                "images": ids,
                "image_labels": {ids[0]: "粉色", ids[1]: "黑色"} if labels else {},
            },
            {"code": "S1", "name": "公园", "kind": "place", "appearance": "草坪"},
        ],
        "scene": "",
        "style": "",
        "avoid": "",
    }
    assert client.patch(f"/api/v2/projects/{pid}", json={"look": look}).status_code == 200
    client.patch(
        f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"mode": "ai", "prompt": "P1 挂在衣架上"}}
    )
    return pid, sid, look


def test_ai_generation_waits_for_confirmed_materials(client, settings, tmp_path):
    pid, sid, _look = _project(client, tmp_path, labels=False)
    project = client.get(f"/api/v2/projects/{pid}").json()
    assert project["look"]["confirmed"] is False
    blocked = client.post(f"/api/v2/projects/{pid}/shots/{sid}/candidates", json={"confirm_charge": True})
    assert blocked.status_code == 409 and "素材" in blocked.json()["detail"]
    assert client.post(f"/api/v2/projects/{pid}/renders", json={"confirm_charge": True}).status_code == 409
    # 不花钱的来源不受影响
    client.patch(f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"mode": "color"}})
    # 没准备好时确认失败，并说明缺什么（场景主体不需要参考图）
    refused = client.post(f"/api/v2/projects/{pid}/look/confirm")
    assert (
        refused.status_code == 409
        and "颜色标注" in refused.json()["detail"]
        and "S1" not in refused.json()["detail"]
    )


def test_confirm_then_client_cannot_reset_it_and_edits_are_flagged(client, settings, tmp_path):
    pid, sid, look = _project(client, tmp_path)
    confirmed = client.post(f"/api/v2/projects/{pid}/look/confirm")
    assert confirmed.status_code == 200 and confirmed.json()["look"]["confirmed"] is True
    # 客户端整体替换全片设定时带了 confirmed=false 也改不掉；内容没变不记修改时间
    same = client.patch(f"/api/v2/projects/{pid}", json={"look": {**look, "confirmed": False}}).json()["look"]
    assert same["confirmed"] is True and same["revised_at"] is None
    changed = {
        **look,
        "subjects": [{**look["subjects"][0], "appearance": "立领无帽，绗缝"}, look["subjects"][1]],
    }
    revised = client.patch(f"/api/v2/projects/{pid}", json={"look": changed}).json()["look"]
    assert revised["confirmed"] is True and revised["revised_at"] > 0
    # 再确认一次清掉修改提示
    assert client.post(f"/api/v2/projects/{pid}/look/confirm").json()["look"]["revised_at"] is None


def test_subject_without_reference_can_be_marked_and_old_projects_are_not_blocked(client, store, tmp_path):
    from shotwright.core.models import Look, LookSubject, Project, Shot, ShotVisual
    from shotwright.services import readiness

    project = Project(shots=[Shot(text="x", visual=ShotVisual(mode="ai", prompt="A 走过"))])
    project.look = Look(subjects=[LookSubject(code="A", name="路人", kind="person", appearance="中年男子")])
    assert project.look.confirmed is None and not readiness.generation_locked(project)  # 旧项目不拦
    project.look.confirmed = False
    assert readiness.generation_locked(project)
    with pytest.raises(ValueError, match="参考图"):
        readiness.confirm(project)
    project.look.subjects[0].no_reference = True
    readiness.confirm(project)
    assert not readiness.generation_locked(project)
    # 只有场景类主体的视频不需要确认
    plain = Project(look=Look(subjects=[LookSubject(code="S1", kind="place")], confirmed=False))
    assert not readiness.generation_locked(plain)
