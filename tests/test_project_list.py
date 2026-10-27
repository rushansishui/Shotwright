"""首页项目列表：最近的成片、封面。"""

import io

from PIL import Image


def test_list_has_cover_and_render_info(client):
    created = client.post(
        "/api/v2/projects", json={"mode": "split", "script": "一句话。", "voice": {"provider": "silent"}}
    ).json()
    pid, sid = created["id"], created["shots"][0]["id"]
    summary = next(p for p in client.get("/api/v2/projects").json() if p["id"] == pid)
    assert summary["cover"] is None and summary["render"] is None
    assert client.get(f"/api/v2/projects/{pid}/cover").status_code == 404

    buf = io.BytesIO()
    Image.new("RGB", (900, 1600), "#336699").save(buf, "PNG")
    asset = client.post(f"/api/v2/projects/{pid}/assets", files={"file": ("c.png", buf.getvalue(), "image/png")}).json()
    client.patch(f"/api/v2/projects/{pid}/shots/{sid}", json={"visual": {"mode": "upload", "selected": asset["id"]}})
    summary = next(p for p in client.get("/api/v2/projects").json() if p["id"] == pid)
    assert summary["cover"] == f"/api/v2/projects/{pid}/cover?k=a{asset['id']}"
    response = client.get(summary["cover"])
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(response.content))
    assert image.width == 480
