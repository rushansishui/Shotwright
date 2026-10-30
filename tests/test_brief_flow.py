"""新建视频的引导流程：想法 → 诉求 → 脚本 → 分镜，以及上传素材的归位。"""

import json

from conftest import make_clip, make_image

from shotwright.media.ffmpeg import run_ffmpeg
from shotwright.providers import registry
from shotwright.providers.base import ProviderSpec


class FlowLLM:
    seen: list[list[dict]] = []

    def __init__(self, _settings):
        pass

    def complete(self, messages, *, json_mode=False):
        FlowLLM.seen.append(messages)
        system = messages[0]["content"]
        if "诉求模板" in system:
            return "用途：在抖音投放的新品推广广告。\n价格与引导：【待补充：价格】"
        if "口播脚本" in system:
            return json.dumps(
                {
                    "title": "轻薄羽绒服",
                    "lines": ["冬天出门怕冷？", "这件羽绒服只有三百克。", "点击购物车。"],
                },
                ensure_ascii=False,
            )
        shots = [
            {
                "role": "hook",
                "text": "被改写的文案",
                "terms": ["snow"],
                "prompt": "P1 " + "画面" * 60,
                "still": "静止",
                "camera": "static",
                "link": "开场",
            },
            {
                "role": "demo",
                "text": "另一段",
                "terms": ["coat"],
                "prompt": "P1 " + "画面" * 60,
                "still": "静止",
                "camera": "pan",
                "link": "接上",
                "footage": "M2",
            },
            {
                "role": "cta",
                "text": "结尾",
                "terms": ["cart"],
                "prompt": "P1 " + "画面" * 60,
                "still": "静止",
                "camera": "static",
                "link": "收尾",
            },
            {
                "role": "cta",
                "text": "多出来的",
                "terms": ["x"],
                "prompt": "P1 " + "画面" * 60,
                "still": "静止",
                "camera": "static",
                "link": "多余",
            },
        ]
        look = {
            "subjects": [{"code": "P1", "name": "羽绒服", "kind": "product", "appearance": "米白色立领"}],
            "scene": "雪地",
            "style": "明亮",
            "avoid": "文字",
            "grade": "natural",
        }
        return json.dumps({"title": "AI 标题", "look": look, "shots": shots}, ensure_ascii=False)


def _use_flow_llm(settings):
    registry.specs()  # 先加载内置服务，测试用的服务排在后面，不影响按顺序列出预设的测试
    registry.register(ProviderSpec(kind="llm", id="flow", name="F", offline=True), FlowLLM)
    settings.defaults["llm"] = "flow"
    FlowLLM.seen.clear()


def test_normalize_and_script(client, settings):
    _use_flow_llm(settings)
    body = {
        "idea": "卖一件儿童羽绒服，米白和雾霾蓝两色，很轻",
        "template": "用途：在【平台】投放的【新品推广】广告。\n价格与引导：【价格】",
        "video_type": "product_ad",
        "duration": 30,
        "materials": [{"kind": "image", "role": "product", "name": "coat.jpg", "note": "正面"}],
    }
    brief = client.post("/api/v2/brief/normalize", json=body).json()["brief"]
    assert "【待补充：价格】" in brief
    system, user = FlowLLM.seen[-1][0]["content"], FlowLLM.seen[-1][1]["content"]
    assert "【平台】" in system and "商品广告" in system
    assert "M1，图片，产品图，文件名 coat.jpg，说明：正面" in user and "很轻" in user

    script = client.post(
        "/api/v2/brief/script", json={"brief": brief, "video_type": "product_ad", "duration": 30}
    ).json()
    assert script["title"] == "轻薄羽绒服" and len(script["lines"]) == 3 and script["seconds"] > 0


def test_storyboard_follows_script_and_places_materials(client, settings, tmp_path):
    _use_flow_llm(settings)
    project = client.post(
        "/api/v2/projects",
        json={"mode": "empty", "video_type": "product_ad", "voice": {"provider": "silent"}},
    ).json()
    pid = project["id"]

    def upload(path, as_audio=False):
        with path.open("rb") as handle:
            url = f"/api/v2/projects/{pid}/assets" + ("?as_audio=true" if as_audio else "")
            return client.post(url, files={"file": (path.name, handle)}).json()["id"]

    audio = tmp_path / "bgm.m4a"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", str(audio)])
    image = upload(make_image(tmp_path / "coat.png"))
    video = upload(make_clip(tmp_path / "clip.mp4"))
    music = upload(audio, as_audio=True)
    materials = [
        {"kind": "image", "role": "product", "name": "coat.png", "asset_id": image},
        {"kind": "video", "role": "footage", "name": "clip.mp4", "asset_id": video},
        {"kind": "audio", "role": "bgm", "name": "bgm.m4a", "asset_id": music},
    ]
    lines = ["冬天出门怕冷？", "这件羽绒服只有三百克。", "点击购物车。"]
    result = client.post(
        f"/api/v2/projects/{pid}/storyboard",
        json={
            "subject": "儿童羽绒服广告",
            "idea": "卖羽绒服",
            "script": lines,
            "duration": 30,
            "materials": materials,
        },
    )
    assert result.status_code == 200, result.text
    data = result.json()
    assert [shot["text"] for shot in data["shots"]] == lines  # 文案按定稿脚本，不被改写
    assert data["look"]["subjects"][0]["images"] == [image]
    second = data["shots"][1]["visual"]
    assert second["mode"] == "upload" and second["selected"] == video  # 大模型建议的 M2 放在第 2 个镜头
    assert data["bgm"]["asset_id"] == music
    assert data["title"] == "AI 标题" and data["subject"] == "儿童羽绒服广告"
    # 创作记录：原始想法和脚本留在项目里，复制成新项目也带着。
    assert data["idea"] == "卖羽绒服" and data["script"] == lines
    client.patch(f"/api/v2/projects/{pid}/shots/{data['shots'][0]['id']}", json={"text": "改过的文案"})
    copy = client.post(f"/api/v2/projects/{pid}/duplicate", json={}).json()
    assert copy["idea"] == "卖羽绒服" and copy["script"] == lines and copy["subject"] == "儿童羽绒服广告"
    user = FlowLLM.seen[0][1]["content"]
    assert "定稿脚本" in user and "1. 冬天出门怕冷？" in user and "M2，视频，画面素材" in user


def test_materials_for_pasted_script(client, settings, tmp_path):
    project = client.post(
        "/api/v2/projects",
        json={
            "mode": "split",
            "script": "第一句话说得长一些，让它单独成为一个镜头好不好。第二句也要足够长才行，单独成为一个镜头。",
            "voice": {"provider": "silent"},
        },
    ).json()
    pid = project["id"]
    ids = []
    for name in ("a.png", "b.png"):
        with make_image(tmp_path / name).open("rb") as handle:
            ids.append(
                client.post(f"/api/v2/projects/{pid}/assets", files={"file": (name, handle)}).json()["id"]
            )
    materials = [
        {"kind": "image", "role": "footage", "asset_id": ids[0]},
        {"kind": "image", "role": "logo", "note": "Logo", "asset_id": ids[1]},
    ]
    data = client.post(f"/api/v2/projects/{pid}/materials", json={"materials": materials}).json()
    assert data["shots"][0]["visual"]["selected"] == ids[0]
    assert data["references"] == [{"asset_id": ids[1], "role": "logo", "label": "Logo", "enabled": True}]
    bad = client.post(
        f"/api/v2/projects/{pid}/materials",
        json={"materials": [{"kind": "image", "role": "footage", "asset_id": "nosuch123"}]},
    )
    assert bad.status_code == 404


def test_pasted_script_is_kept_as_record(client):
    script = "第一句话说得长一些，让它单独成为一个镜头好不好。第二句也要足够长才行，单独成为一个镜头。"
    project = client.post(
        "/api/v2/projects",
        json={"mode": "split", "script": script, "idea": "随便试试", "voice": {"provider": "silent"}},
    ).json()
    assert project["script"] == [shot["text"] for shot in project["shots"]] and len(project["script"]) == 2
    assert project["idea"] == "随便试试"


def test_creation_record_is_editable(client):
    project = client.post(
        "/api/v2/projects",
        json={
            "mode": "split",
            "script": "一句足够长的话，让它成为一个镜头好不好呢。",
            "voice": {"provider": "silent"},
        },
    ).json()
    pid, text = project["id"], project["shots"][0]["text"]
    data = client.patch(
        f"/api/v2/projects/{pid}",
        json={"idea": "  新想法  ", "subject": "新的诉求", "script": ["第一段", "  ", " 第二段 "]},
    ).json()
    assert (
        data["idea"] == "新想法" and data["subject"] == "新的诉求" and data["script"] == ["第一段", "第二段"]
    )
    assert data["shots"][0]["text"] == text  # 只改记录，不动镜头
    assert client.patch(f"/api/v2/projects/{pid}", json={"script": ["x"] * 61}).status_code == 422


def test_storyboard_without_llm_uses_own_script(client, settings, tmp_path):
    """没配大模型：自己写的脚本一段一个镜头，上传的产品图直接当画面，没有画面的镜头用纯色底，能直接渲染。"""
    settings.defaults["llm"] = ""
    settings.defaults["stock"] = ""
    project = client.post(
        "/api/v2/projects",
        json={"mode": "empty", "title": "我的羽绒服", "voice": {"provider": "silent"}},
    ).json()
    pid = project["id"]
    with make_image(tmp_path / "coat.png").open("rb") as handle:
        image = client.post(f"/api/v2/projects/{pid}/assets", files={"file": ("coat.png", handle)}).json()[
            "id"
        ]
    lines = ["冬天出门怕冷？", "这件羽绒服只有三百克。", "点击购物车。"]
    result = client.post(
        f"/api/v2/projects/{pid}/storyboard",
        json={
            "subject": "",
            "script": lines,
            "materials": [{"kind": "image", "role": "product", "name": "coat.png", "asset_id": image}],
        },
    )
    assert result.status_code == 200, result.text
    shots = result.json()["shots"]
    assert [shot["text"] for shot in shots] == lines
    assert shots[0]["visual"]["mode"] == "upload" and shots[0]["visual"]["selected"] == image
    assert [shot["visual"]["mode"] for shot in shots[1:]] == ["color", "color"]
    assert shots[1]["visual"]["color"] != shots[2]["visual"]["color"]
    assert result.json()["title"] == "我的羽绒服"

    # 没有脚本、只有诉求时仍然需要大模型。
    refused = client.post(f"/api/v2/projects/{pid}/storyboard", json={"subject": "随便写写"})
    assert refused.status_code >= 400

    job = client.post(f"/api/v2/projects/{pid}/renders", json={}).json()
    assert client.get(f"/api/v2/renders/{job['id']}").json()["state"] == "done"
