"""同一个视频里人物、产品在各镜头保持一致：参考图、定妆图、默认画面来源和渲染前体检。"""

import json
from pathlib import Path

import httpx
import pytest
from conftest import FakeImageGen, make_clip, make_image

from shotwright.config import Settings
from shotwright.core.models import Look, LookSubject, Project, Shot, ShotVisual
from shotwright.providers import registry
from shotwright.providers.base import ProviderSpec, ReferenceImagesUnsupported
from shotwright.providers.video_gen.seedance import SeedanceVideo
from shotwright.services import checkup, prompting, storyboard


def _seedance(handler) -> SeedanceVideo:
    provider = SeedanceVideo({"api_key": "k", "poll_interval": "0"})
    provider._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return provider


def test_seedance_sends_reference_images_and_reports_rejection(tmp_path):
    refs = [make_image(tmp_path / "a.png"), make_image(tmp_path / "b.png")]
    payloads = []

    def handler(request):
        if request.method == "POST":
            payload = json.loads(request.content)
            payloads.append(payload)
            if any(item.get("role") == "reference_image" for item in payload["content"]):
                return httpx.Response(
                    400,
                    json={"error": {"code": "InvalidParameter", "message": "reference_image not supported"}},
                )
            return httpx.Response(200, json={"id": "t1"})
        if request.url.path.endswith("/tasks/t1"):
            return httpx.Response(
                200, json={"status": "succeeded", "content": {"video_url": "https://cdn.example/v.mp4"}}
            )
        return httpx.Response(200, content=b"mp4")

    # 能带参考图的服务：一次带两张。
    ok_payloads = []

    def accepts(request):
        if request.method == "POST":
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "t1"})
        if request.url.path.endswith("/tasks/t1"):
            return httpx.Response(
                200, json={"status": "succeeded", "content": {"video_url": "https://cdn.example/v.mp4"}}
            )
        return httpx.Response(200, content=b"mp4")

    _seedance(accepts).generate(
        "P1 在雪地", aspect="9:16", duration=5, output=tmp_path / "v", reference_images=refs
    )
    roles = [item.get("role") for item in payloads[0]["content"]]
    assert roles == [None, "reference_image", "reference_image"] and payloads[0]["ratio"] == "9:16"
    assert not ok_payloads

    # 不支持参考图的服务：provider 只报「不接受」，由调用方去掉提示词里的参考图要求后重来。
    with pytest.raises(ReferenceImagesUnsupported):
        _seedance(handler).generate(
            "P1 在雪地", aspect="9:16", duration=5, output=tmp_path / "w", reference_images=refs
        )
    assert len(payloads) == 2  # 只提交了一次（没有在 provider 里偷偷重试）


class RefVideo:
    calls: list[dict] = []

    def __init__(self, _settings):
        pass

    def generate(
        self,
        prompt,
        *,
        aspect,
        duration,
        output,
        first_frame=None,
        reference_video=None,
        reference_images=None,
        on_task=None,
    ):
        RefVideo.calls.append(
            {"prompt": prompt, "first_frame": first_frame, "refs": list(reference_images or [])}
        )
        return make_clip(output.with_suffix(".mp4"))


def _upload(client, pid, path):
    with path.open("rb") as handle:
        return client.post(
            f"/api/v2/projects/{pid}/assets", files={"file": (path.name, handle, "image/png")}
        ).json()["id"]


def _project(client, tmp_path, images=True):
    created = client.post(
        "/api/v2/projects", json={"mode": "split", "script": "一句话。", "voice": {"provider": "silent"}}
    ).json()
    pid, sid = created["id"], created["shots"][0]["id"]
    # 尺寸不同，内容才不同（同样内容的上传会合并成一个素材）。
    sizes = {"coat": "640x360", "girl": "400x400"}
    ids = (
        [_upload(client, pid, make_image(tmp_path / f"{n}.png", size=size)) for n, size in sizes.items()]
        if images
        else []
    )
    look = {
        "subjects": [
            {
                "code": "P1",
                "name": "羽绒服",
                "kind": "product",
                "appearance": "米白色立领",
                "images": ids[:1],
            },
            {"code": "M1", "name": "小女孩", "kind": "person", "appearance": "短发", "images": ids[1:]},
        ],
        "scene": "",
        "style": "",
        "avoid": "",
    }
    assert client.patch(f"/api/v2/projects/{pid}", json={"look": look}).status_code == 200
    return pid, sid, ids


def test_ai_video_uses_reference_images_not_first_frame(client, settings, tmp_path):
    registry.register(ProviderSpec(kind="video_gen", id="refvid", name="RV", paid=True), RefVideo)
    settings.defaults["video_gen"] = "refvid"
    pid, sid, _ids = _project(client, tmp_path)
    client.patch(
        f"/api/v2/projects/{pid}/shots/{sid}",
        json={"visual": {"mode": "ai", "prompt": "M1 穿着 P1 在雪地里奔跑"}},
    )
    response = client.post(f"/api/v2/projects/{pid}/shots/{sid}/candidates", json={"confirm_charge": True})
    assert response.status_code == 200, response.text
    call = RefVideo.calls[-1]
    assert call["first_frame"] is None and len(call["refs"]) == 2  # P1、M1 各一张
    assert "参考图 1 = P1 羽绒服" in call["prompt"] and "参考图 2 = M1 小女孩" in call["prompt"]


def test_subjects_in_a_shot_default_to_ai():
    look = Look(subjects=[LookSubject(code="M1", kind="person", appearance="短发")])
    shots = [
        Shot(text="a", role="people", visual=ShotVisual(prompt="M1 走在街上")),
        Shot(text="b", role="scene", visual=ShotVisual(prompt="城市夜景")),
    ]
    storyboard.suggest_sources(shots, {"ai"}, look)
    assert [s.visual.mode for s in shots] == ["ai", "stock"]


def test_checkup_flags_inconsistent_subjects():
    project = Project(
        look=Look(
            subjects=[
                LookSubject(code="P1", name="羽绒服", kind="product"),
                LookSubject(code="S1", kind="place"),
            ]
        ),
        shots=[
            Shot(text="a", visual=ShotVisual(mode="ai", prompt="P1 特写")),
            Shot(text="b", visual=ShotVisual(mode="stock", prompt="P1 挂在衣架上")),
        ],
    )
    codes = {(issue.code, issue.shot) for issue in checkup.check_project(project, Settings())}
    assert ("subject_no_image", None) in codes and ("subject_in_stock", 2) in codes


def test_subject_image_is_generated_with_confirmation(client, settings, tmp_path):
    pid, _sid, _ids = _project(client, tmp_path, images=False)
    assert client.post(f"/api/v2/projects/{pid}/look/P1/image", json={}).status_code == 402
    data = client.post(f"/api/v2/projects/{pid}/look/P1/image", json={"confirm_charge": True}).json()
    (image,) = data["look"]["subjects"][0]["images"]
    assert data["assets"][image]["kind"] == "image"
    assert "米白色立领" in FakeImageGen.calls[-1] and "浅灰色背景" in FakeImageGen.calls[-1]
    assert (
        client.post(f"/api/v2/projects/{pid}/look/NOPE/image", json={"confirm_charge": True}).status_code
        == 404
    )


def test_portrait_prompt_for_person():
    project = Project(
        look=Look(subjects=[LookSubject(code="M1", name="小女孩", kind="person", appearance="8 岁，短发")])
    )
    text = prompting.subject_portrait_prompt(project, "M1")
    assert text.startswith("小女孩的定妆照") and "8 岁，短发" in text


def _variants(client, tmp_path):
    created = client.post(
        "/api/v2/projects", json={"mode": "split", "script": "一句话。", "voice": {"provider": "silent"}}
    ).json()
    pid, sid = created["id"], created["shots"][0]["id"]
    sizes = {"white": "320x320", "cream": "330x330", "pink": "340x340", "black": "350x350"}
    ids = {
        name: _upload(client, pid, make_image(tmp_path / f"{name}.png", size=size))
        for name, size in sizes.items()
    }
    labels = {ids["white"]: "白色", ids["pink"]: "浅粉色", ids["black"]: "黑色"}
    look = {
        "subjects": [
            {
                "code": "P1",
                "name": "儿童羽绒服",
                "kind": "product",
                "appearance": "浅粉、米白、蓝、黑四色",
                "images": list(ids.values()),
                "image_labels": labels,
            }
        ],
        "scene": "",
        "style": "",
        "avoid": "",
    }
    assert client.patch(f"/api/v2/projects/{pid}", json={"look": look}).status_code == 200
    return pid, sid, ids


def test_multi_color_product_uses_only_the_main_image_by_default(client, settings, tmp_path):
    registry.register(ProviderSpec(kind="video_gen", id="refvid", name="RV", paid=True), RefVideo)
    settings.defaults["video_gen"] = "refvid"
    pid, sid, _ids = _variants(client, tmp_path)
    client.patch(
        f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"mode": "ai", "prompt": "P1 挂在衣架上"}}
    )
    client.post(f"/api/v2/projects/{pid}/shots/{sid}/candidates", json={"confirm_charge": True})
    call = RefVideo.calls[-1]
    assert len(call["refs"]) == 1  # 四种颜色只带主图，不让模型混着来
    assert "参考图 1 = P1 儿童羽绒服（白色款）" in call["prompt"]
    assert (
        "这个镜头里是「白色」款" in call["prompt"]
        and "不要按文字描述或自行发挥换成别的颜色" in call["prompt"]
    )


def test_shot_can_pick_another_color(client, settings, tmp_path):
    registry.register(ProviderSpec(kind="video_gen", id="refvid", name="RV", paid=True), RefVideo)
    settings.defaults["video_gen"] = "refvid"
    pid, sid, ids = _variants(client, tmp_path)
    response = client.patch(
        f"/api/v2/projects/{pid}/shots/{sid}",
        json={"visual": {"mode": "ai", "prompt": "P1 挂在衣架上", "refs": [ids["pink"]]}},
    )
    assert response.json()["shots"][0]["visual"]["refs"] == [ids["pink"]]
    client.post(f"/api/v2/projects/{pid}/shots/{sid}/candidates", json={"confirm_charge": True})
    prompt = RefVideo.calls[-1]["prompt"]
    assert (
        "（浅粉色款）" in prompt
        and "这个镜头里是「浅粉色」款" in prompt
        and "白色" not in prompt.split("参考图")[0][-200:]
    )
    audio = tmp_path / "a.m4a"
    from shotwright.media.ffmpeg import run_ffmpeg

    run_ffmpeg(["-f", "lavfi", "-i", "sine=duration=1", "-c:a", "aac", str(audio)])
    with audio.open("rb") as handle:
        sound = client.post(
            f"/api/v2/projects/{pid}/assets?as_audio=true", files={"file": ("a.m4a", handle)}
        ).json()["id"]
    assert (
        client.patch(f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"refs": [sound]}}).status_code
        == 422
    )


def test_labels_of_removed_images_are_dropped():
    subject = LookSubject.model_validate(
        {
            "code": "P1",
            "images": ["aaa111"],
            "image_labels": {"aaa111": " 粉 ", "bbb222": "黑", "aaa111x": ""},
        }
    )
    assert subject.image_labels == {"aaa111": "粉"}


class BigRefVideo(RefVideo):
    max_reference_images = 9


def test_shot_naming_several_colors_gets_those_variants_and_shape_rule(client, settings, tmp_path):
    registry.register(ProviderSpec(kind="video_gen", id="bigref", name="BR", paid=True), BigRefVideo)
    settings.defaults["video_gen"] = "bigref"
    pid, sid, ids = _variants(client, tmp_path)
    look = client.get(f"/api/v2/projects/{pid}").json()["look"]
    kid = _upload(client, pid, make_image(tmp_path / "kid.png", size="360x360"))
    look["subjects"].append(
        {"code": "A", "name": "女孩", "kind": "person", "appearance": "6岁", "images": [kid]}
    )
    client.patch(f"/api/v2/projects/{pid}", json={"look": look})
    client.patch(
        f"/api/v2/projects/{pid}/shots/{sid}",
        json={"visual": {"mode": "ai", "prompt": "A 和两个小朋友分别穿浅粉和黑色的 P1"}},
    )
    client.post(f"/api/v2/projects/{pid}/shots/{sid}/candidates", json={"confirm_charge": True})
    call = RefVideo.calls[-1]
    assert len(call["refs"]) == 3  # 浅粉、黑色两款 + 人物
    prompt = call["prompt"]
    assert "参考图 1 = P1 儿童羽绒服（浅粉色款）" in prompt and "参考图 2 = P1 儿童羽绒服（黑色款）" in prompt
    assert "参考图 3 = A 女孩" in prompt and "不要照搬参考图里的衣服" in prompt
    assert "对应的参考图（1、2）" in prompt and "有没有帽子" in prompt
    assert "这个镜头里出现「浅粉色、黑色」这几款" in prompt
    assert ids["white"] not in ",".join(str(p) for p in call["refs"])


def test_seedance_reference_limit_depends_on_model():
    assert SeedanceVideo({"api_key": "k"}).max_reference_images == 9
    assert (
        SeedanceVideo({"api_key": "k", "model": "doubao-seedance-1-0-lite-i2v-250428"}).max_reference_images
        == 4
    )


class _Describer:
    prompts: list[tuple[str, int]] = []

    def __init__(self, _settings):
        self.vision_model = "fake-vision"

    def complete(self, messages, *, json_mode=False):
        return "{}"

    def see(self, prompt, images):
        _Describer.prompts.append((prompt, len(images)))
        return '{"appearance": "立领无帽短款，正面通体拉链，横向直线绗缝，左胸小字印花，袖口下摆松紧收口"}'


def test_describe_subject_from_images(client, settings, tmp_path):
    pid, _sid, _ids = _variants(client, tmp_path)
    settings.defaults["llm"] = "none-such"
    assert client.post(f"/api/v2/projects/{pid}/look/P1/describe").status_code >= 400  # 没有看图模型
    registry.register(ProviderSpec(kind="llm", id="describer", name="D", offline=True), _Describer)
    settings.defaults["llm"] = "describer"
    response = client.post(f"/api/v2/projects/{pid}/look/P1/describe")
    assert response.status_code == 200 and "无帽" in response.json()["appearance"]
    prompt, count = _Describer.prompts[-1]
    assert count == 4 and "有没有帽子" in prompt and "颜色不用写" in prompt
    assert client.post(f"/api/v2/projects/{pid}/look/ZZ/describe").status_code == 404


def _plain_jpeg(path, color):
    from PIL import Image

    Image.new("RGB", (400, 400), color).save(path, quality=90)  # 普通 JPEG：ffmpeg 用 image2 读取
    return path


def test_jpeg_reference_images_are_actually_sent(client, settings, tmp_path):
    """回归：普通 JPEG 加 -ss 取不到画面，参考图曾被全部跳过，模型只能按文字自己画（加了帽子）。"""
    from shotwright.media.ffmpeg import extract_frame

    jpeg = _plain_jpeg(tmp_path / "coat.jpg", (250, 200, 210))
    assert extract_frame(jpeg, tmp_path / "a.jpg", at=0.0, width=1536).exists()
    assert extract_frame(jpeg, tmp_path / "b.jpg", width=1024).exists()  # 不带 at：图片也不能 -sseof

    registry.register(ProviderSpec(kind="video_gen", id="refvid", name="RV", paid=True), RefVideo)
    settings.defaults["video_gen"] = "refvid"
    created = client.post(
        "/api/v2/projects", json={"mode": "split", "script": "一句话。", "voice": {"provider": "silent"}}
    ).json()
    pid, sid = created["id"], created["shots"][0]["id"]
    image = _upload(client, pid, jpeg)
    look = {
        "subjects": [
            {"code": "P1", "name": "羽绒服", "kind": "product", "appearance": "立领无帽", "images": [image]}
        ],
        "scene": "",
        "style": "",
        "avoid": "",
    }
    client.patch(f"/api/v2/projects/{pid}", json={"look": look})
    client.patch(
        f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"mode": "ai", "prompt": "P1 挂在衣架上"}}
    )
    response = client.post(f"/api/v2/projects/{pid}/shots/{sid}/candidates", json={"confirm_charge": True})
    assert response.status_code == 200
    assert len(RefVideo.calls[-1]["refs"]) == 1
    asset = response.json()["assets"][response.json()["shots"][0]["visual"]["selected"]]
    assert asset["meta"]["references"] == image


def test_generation_stops_when_no_reference_can_be_read(client, settings, tmp_path, monkeypatch):
    from shotwright.media.ffmpeg import MediaError
    from shotwright.services import visuals

    registry.register(ProviderSpec(kind="video_gen", id="refvid", name="RV", paid=True), RefVideo)
    settings.defaults["video_gen"] = "refvid"
    pid, sid, _ids = _variants(client, tmp_path)
    client.patch(
        f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"mode": "ai", "prompt": "P1 挂在衣架上"}}
    )

    def broken(*_args, **_kwargs):
        raise MediaError("没有取到画面")

    monkeypatch.setattr(visuals, "extract_frame", broken)
    before = len(RefVideo.calls)
    response = client.post(f"/api/v2/projects/{pid}/shots/{sid}/candidates", json={"confirm_charge": True})
    assert response.status_code >= 400 and "参考图都无法读取" in response.text
    assert len(RefVideo.calls) == before  # 没有提交收费任务


def test_unreadable_reference_keeps_numbering_in_sync(client, settings, tmp_path, monkeypatch):
    """回归：中间一张参考图读不出来时，提示词里的编号要和真正发出去的图片一致。

    否则「参考图 2」会指向另一张图，模型照着一张错位的照片生成，各镜头的人物就对不上。
    """
    from shotwright.media.ffmpeg import MediaError
    from shotwright.services import visuals

    registry.register(ProviderSpec(kind="video_gen", id="refvid", name="RV", paid=True), RefVideo)
    settings.defaults["video_gen"] = "refvid"
    created = client.post(
        "/api/v2/projects", json={"mode": "split", "script": "一句话。", "voice": {"provider": "silent"}}
    ).json()
    pid, sid = created["id"], created["shots"][0]["id"]
    # 三张颜色不同的图（内容不同的素材才不会被按 SHA-256 合并成一个）。
    colours = [(200, 100, 100), (100, 200, 100), (100, 100, 200)]
    ids = [
        _upload(client, pid, _plain_jpeg(tmp_path / f"{name}.jpg", colour))
        for name, colour in zip("abc", colours, strict=True)
    ]
    assert len(set(ids)) == 3
    look = {
        "subjects": [
            {"code": "P1", "name": "羽绒服", "kind": "product", "appearance": "立领", "images": ids}
        ],
        "scene": "",
        "style": "",
        "avoid": "",
    }
    client.patch(f"/api/v2/projects/{pid}", json={"look": look})
    # 三张图全部带上（镜头显式选了），避免默认只取主图。
    client.patch(
        f"/api/v2/projects/{pid}/shots/{sid}",
        json={"visual": {"mode": "ai", "prompt": "P1 特写", "refs": ids}},
    )

    real = visuals.extract_frame
    broken = {ids[1]}

    def sometimes_broken(source, *args, **kwargs):
        if Path(source).stem in {i[:16] for i in broken}:
            raise MediaError("没有取到画面")
        return real(source, *args, **kwargs)

    monkeypatch.setattr(visuals, "extract_frame", sometimes_broken)
    response = client.post(f"/api/v2/projects/{pid}/shots/{sid}/candidates", json={"confirm_charge": True})
    assert response.status_code == 200, response.text
    call = RefVideo.calls[-1]
    assert len(call["refs"]) == 2  # 中间那张读不出来，跳过后仍有 2 张
    prompt = call["prompt"]
    assert "参考图 1" in prompt and "参考图 2" in prompt and "参考图 3" not in prompt
