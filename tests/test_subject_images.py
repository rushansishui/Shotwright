"""全片设定里的主体参考图：上传产品实拍、人物照片，生成 AI 画面时作为参考。"""

import base64
import json

import httpx
import pytest
from conftest import FakeVideoGen, make_image

from shotwright.core.models import LookSubject
from shotwright.providers import registry
from shotwright.providers.base import ProviderSpec, UnconfirmedTaskError
from shotwright.providers.image_gen.openai_images import SEEDREAM_SIZES, OpenAIImages


def test_old_single_reference_migrates_to_images():
    subject = LookSubject.model_validate({"code": "P1", "reference": "abc123"})
    assert subject.images == ["abc123"]
    assert "reference" not in subject.model_dump()
    assert LookSubject.model_validate({"code": "P1", "reference": None}).images == []


def _project_with_subjects(client, tmp_path):
    created = client.post(
        "/api/v2/projects", json={"mode": "split", "script": "一句话。", "voice": {"provider": "silent"}}
    ).json()
    pid, sid = created["id"], created["shots"][0]["id"]
    ids = []
    for name in ("product", "person"):
        path = make_image(tmp_path / f"{name}.png", size="800x800")
        with path.open("rb") as handle:
            ids.append(client.post(f"/api/v2/projects/{pid}/assets", files={"file": (path.name, handle, "image/png")}).json()["id"])
    look = {
        "subjects": [
            {"code": "P1", "name": "羽绒服", "kind": "product", "appearance": "米白色", "images": [ids[0]]},
            {"code": "M1", "name": "小女孩", "kind": "person", "appearance": "短发", "images": [ids[1]]},
        ],
        "scene": "",
        "style": "",
        "avoid": "",
    }
    response = client.patch(f"/api/v2/projects/{pid}", json={"look": look})
    assert response.status_code == 200, response.text
    return pid, sid, ids


def test_video_uses_the_photo_of_the_subject_in_the_shot(client, settings, tmp_path):
    pid, sid, (product, person) = _project_with_subjects(client, tmp_path)
    client.patch(f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"mode": "ai", "prompt": "M1 在雪地里奔跑"}})
    response = client.post(f"/api/v2/projects/{pid}/shots/{sid}/candidates", json={"confirm_charge": True})
    assert response.status_code == 200, response.text
    call = FakeVideoGen.calls[-1]
    assert call["first_frame"] is not None and person in str(call["first_frame"])
    assert "Person reference" in call["prompt"] or "人物参考" in call["prompt"]


class RefImageGen:
    calls: list[list] = []

    def __init__(self, _settings):
        pass

    def generate(self, prompt, *, aspect, output, references=None):
        RefImageGen.calls.append(list(references or []))
        return make_image(output.with_suffix(".png"))


def test_image_generation_passes_subject_photos(client, settings, tmp_path):
    registry.register(ProviderSpec(kind="image_gen", id="refimg", name="R", paid=True), RefImageGen)
    settings.defaults["image_gen"] = "refimg"
    pid, sid, _ids = _project_with_subjects(client, tmp_path)
    client.patch(f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"mode": "image", "prompt": "P1 平铺在木桌上"}})
    response = client.post(f"/api/v2/projects/{pid}/shots/{sid}/candidates", json={"confirm_charge": True})
    assert response.status_code == 200, response.text
    (reference,) = RefImageGen.calls[-1]  # 只带画面里提到的 P1
    assert reference.suffix == ".jpg"


def test_look_images_must_be_existing_images(client, tmp_path):
    pid, _sid, _ids = _project_with_subjects(client, tmp_path)
    bad = {"subjects": [{"code": "P1", "name": "", "kind": "product", "appearance": "", "images": ["nosuchasset1"]}]}
    response = client.patch(f"/api/v2/projects/{pid}", json={"look": {**bad, "scene": "", "style": "", "avoid": ""}})
    assert response.status_code == 422


def _seedream() -> OpenAIImages:
    settings = {"api_key": "k", "model": "doubao-seedream-5-0-pro-260628", "base_url": "https://ark.example/api/v3"}
    return OpenAIImages(settings, label="Seedream", sizes=SEEDREAM_SIZES)


def test_seedream_sends_references_and_retries_without(tmp_path, monkeypatch):
    png = make_image(tmp_path / "out.png").read_bytes()
    refs = [make_image(tmp_path / "r1.jpg"), make_image(tmp_path / "r2.jpg")]
    payloads = []

    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if "image" in payload and len(payloads) == 1 and reject["first"]:
            return httpx.Response(400, json={"error": {"code": "InvalidParameter", "message": "image not supported"}})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png).decode()}]})

    real = httpx.Client
    monkeypatch.setattr(
        "shotwright.providers.image_gen.openai_images.httpx.Client",
        lambda **kw: real(transport=httpx.MockTransport(handler)),
    )
    reject = {"first": False}
    _seedream().generate("羽绒服", aspect="9:16", output=tmp_path / "a", references=refs)
    assert isinstance(payloads[0]["image"], list) and payloads[0]["image"][0].startswith("data:image/jpeg;base64,")

    payloads.clear()
    reject["first"] = True
    out = _seedream().generate("羽绒服", aspect="9:16", output=tmp_path / "b", references=refs[:1])
    assert out.is_file()
    assert isinstance(payloads[0]["image"], str) and "image" not in payloads[1]  # 被拒后去掉参考图重试


def test_seedream_does_not_retry_when_charge_is_unknown(tmp_path, monkeypatch):
    real = httpx.Client
    count = []
    monkeypatch.setattr(
        "shotwright.providers.image_gen.openai_images.httpx.Client",
        lambda **kw: real(transport=httpx.MockTransport(lambda r: count.append(1) or httpx.Response(500))),
    )
    with pytest.raises(UnconfirmedTaskError):
        _seedream().generate("x", aspect="1:1", output=tmp_path / "c", references=[make_image(tmp_path / "r.png")])
    assert len(count) == 1
