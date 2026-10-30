"""生成前检查（services/precheck.py）和按文案拆镜头（editing.split_text）。"""

from __future__ import annotations

import pytest

from shotwright.core.models import Project, Shot, ShotVisual
from shotwright.services import checkup, editing, precheck, visuals


def codes(text: str) -> list[str]:
    return [risk.code for risk in precheck.prompt_risks(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("女孩用手指轻点手机屏幕", ["hands"]),
        ("close-up of hands holding a cup", ["hands"]),
        ("招牌上写着欢迎光临", ["text"]),
        ("a neon sign that says OPEN", ["text"]),
        ("两个人握手", ["people"]),
        ("a group of friends hugging", ["people"]),
        ("她在雨中奔跑", ["motion"]),
        ("快速推近", ["camera"]),
        ("a woman waves, then smiles, then walks away", ["sequence"]),
    ],
)
def test_prompt_risks_find_risky_content(text, expected):
    assert codes(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "产品放在白色桌面上，镜头缓慢推近",
        "产品特写，不要出现任何文字",
        "a bottle on a table, slow push in, without any text",
        "",
    ],
)
def test_prompt_risks_ignore_safe_or_negated_content(text):
    assert codes(text) == []


def test_negation_only_applies_within_the_clause():
    # 「不要」在前一个分句里，管不到后面的文字要求。
    assert codes("不要太暗，招牌上写着欢迎光临") == ["text"]
    # 「不断」不是否定。
    assert codes("她不断奔跑") == ["motion"]


def test_risks_carry_message_and_advice():
    risk = precheck.prompt_risks("手指特写")[0]
    assert risk.message and risk.advice


def test_duration_risk_counts_parts():
    assert precheck.duration_risk(9.0, 10) is None
    assert precheck.duration_risk(10.4, 10) is None  # 差得不多时只是把模型时长往上取
    risk = precheck.duration_risk(23.0, 10)
    assert risk is not None and risk.params == {"seconds": 23.0, "max": 10.0, "parts": 3}
    assert "3 段" in risk.message
    assert precheck.duration_risk(30, None) is None


def _ai_project(text: str, prompt: str = "a bottle on a table") -> tuple[Project, Shot]:
    shot = Shot(text=text, visual=ShotVisual(mode="ai", prompt=prompt))
    return Project(title="t", shots=[shot]), shot


def test_shot_risks_include_duration_for_ai_video_only():
    project, shot = _ai_project("这是一句很长很长的配音。" * 12)
    found = [r.code for r in precheck.shot_risks(project, shot, longest=5)]
    assert found == ["too_long"]
    shot.visual.mode = "image"
    assert precheck.shot_risks(project, shot, longest=5) == []
    shot.visual.mode = "stock"
    shot.visual.prompt = "手指特写"
    assert precheck.shot_risks(project, shot) == []


def test_shot_risks_use_unsaved_prompt():
    project, shot = _ai_project("短句。")
    assert [r.code for r in precheck.shot_risks(project, shot, prompt="两个人握手")] == ["people"]


def test_split_text_cuts_at_the_middle_sentence():
    project, shot = _ai_project("第一句话在这里。第二句话也在这里。第三句话还在这里。第四句话就到这里。")
    shot.visual.camera = "push_in"
    shot.visual.candidates = ["a1"]
    shot.duration = 12
    second = editing.split_text(project, shot.id)
    first = project.shots[0]
    assert first.text == "第一句话在这里。第二句话也在这里。"
    assert second.text == "第三句话还在这里。第四句话就到这里。"
    assert project.shots == [first, second]
    assert second.visual.mode == "ai" and second.visual.prompt == first.visual.prompt
    assert second.visual.camera == "push_in" and second.visual.candidates == []
    assert first.duration is None and second.duration is None
    Project.model_validate(project.model_dump())


def test_split_text_falls_back_to_commas_and_rejects_unbreakable():
    project, shot = _ai_project(
        "A slow morning, coffee on the table, sunlight through the window, a cat asleep."
    )
    second = editing.split_text(project, shot.id)
    assert project.shots[0].text == "A slow morning, coffee on the table,"
    assert second.text == "sunlight through the window, a cat asleep."
    project, shot = _ai_project("一口气说完没有任何停顿的一句话")
    with pytest.raises(ValueError):
        editing.split_text(project, shot.id)
    shot.speech = "original"
    with pytest.raises(ValueError):
        editing.split_text(project, shot.id)


def test_checkup_lists_risks_for_ungenerated_ai_shots(settings, monkeypatch):
    monkeypatch.setattr(visuals, "shot_max_duration", lambda settings, shot: 5.0)
    project, shot = _ai_project("这是一句很长很长的配音。" * 12, prompt="招牌上写着欢迎光临")
    issues = {i.code: i for i in checkup.check_project(project, settings)}
    assert issues["risk_text"].shot == 1 and issues["risk_text"].level == "info"
    assert issues["risk_too_long"].params["parts"] >= 2


def test_precheck_and_split_text_api(client, monkeypatch):
    monkeypatch.setattr(visuals, "shot_max_duration", lambda settings, shot: 5.0)
    project = client.post("/api/v2/projects", json={"mode": "empty", "title": "t"}).json()
    pid = project["id"]
    project = client.post(
        f"/api/v2/projects/{pid}/shots",
        json={"text": "第一句很长很长很长很长。第二句也很长很长很长很长很长很长很长。"},
    ).json()
    sid = project["shots"][-1]["id"]
    client.patch(
        f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"mode": "ai", "prompt": "两个人握手"}}
    )

    risks = client.post(f"/api/v2/projects/{pid}/shots/{sid}/precheck", json={}).json()["risks"]
    assert [r["code"] for r in risks] == ["people", "too_long"]
    unsaved = client.post(
        f"/api/v2/projects/{pid}/shots/{sid}/precheck", json={"prompt": "瓶子放在桌上"}
    ).json()
    assert [r["code"] for r in unsaved["risks"]] == ["too_long"]

    project = client.post(f"/api/v2/projects/{pid}/shots/{sid}/split-text").json()
    texts = [s["text"] for s in project["shots"]][-2:]
    assert texts == ["第一句很长很长很长很长。", "第二句也很长很长很长很长很长很长很长。"]
    assert client.post(f"/api/v2/projects/{pid}/shots/missing1/precheck", json={}).status_code == 404


def test_duplicate_prompt_after_split_is_flagged():
    project, shot = _ai_project("第一句话在这里。第二句话也在这里。", prompt="一只保温杯放在木桌上")
    second = editing.split_text(project, shot.id)
    assert [r.code for r in precheck.shot_risks(project, second)] == ["duplicate"]
    assert precheck.shot_risks(project, project.shots[0]) == []
    # 改了描述就不再提醒；还没保存的描述也算。
    assert precheck.shot_risks(project, second, prompt="特写：杯盖拧开，冒出热气") == []
    second.visual.prompt = "特写：杯盖拧开，冒出热气"
    assert precheck.shot_risks(project, second) == []
