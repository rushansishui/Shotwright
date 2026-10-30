"""设置页：在界面里改 config.toml，保留注释，密钥不回传，立即生效。"""

import tomllib

from fastapi.testclient import TestClient

from shotwright import config_edit
from shotwright.api.app import create_app
from shotwright.config import load_settings

CONFIG = """# 我的配置
[app]
storage_dir = "{storage}"

[defaults]
llm = "openai_compat"

[providers.video_gen.wan]
# api_key = ""
model = "wan2.2-t2v-plus"   # 模型名
"""


def _client(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(CONFIG.format(storage=(tmp_path / "p").as_posix()).replace("\n", "\r\n").encode())
    settings = load_settings(path)
    settings.allowed_hosts = ["testserver"]
    return TestClient(create_app(settings)), path, settings


def test_config_edit_keeps_comments_and_line_endings(tmp_path):
    path = tmp_path / "c.toml"
    path.write_bytes(b'[a]\r\n# x = ""\r\ny = "1"  # note\r\n')
    config_edit.update(path, ["a"], {"x": 'q"\\', "y": "2"})
    raw = path.read_bytes().decode()
    assert "\r\n" in raw and "\n" not in raw.replace("\r\n", "")
    assert "# note" in raw
    assert tomllib.loads(raw)["a"] == {"x": 'q"\\', "y": "2"}
    config_edit.update(path, ["a"], {"y": None})
    config_edit.update(path, ["b", "c"], {"z": "3"})
    data = tomllib.loads(path.read_text())
    assert data == {"a": {"x": 'q"\\'}, "b": {"c": {"z": "3"}}}


def test_save_provider_masks_secret_and_takes_effect(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOTWRIGHT_VIDEO_GEN_WAN_API_KEY", raising=False)
    client, path, settings = _client(tmp_path)
    r = client.put(
        "/api/v2/settings/providers/video_gen/wan", json={"values": {"api_key": "sk-abcdef123456"}}
    )
    assert r.status_code == 200, r.text
    wan = r.json()["providers"]["video_gen"]["wan"]
    assert wan["fields"]["api_key"] == {"set": True, "value": "••••3456"}
    assert "sk-abcdef123456" not in r.text
    assert settings.provider_settings("video_gen", "wan")["api_key"] == "sk-abcdef123456"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["providers"]["video_gen"]["wan"]["api_key"] == "sk-abcdef123456"
    assert "# 模型名" in path.read_text(encoding="utf-8")

    r = client.put("/api/v2/settings/providers/video_gen/wan", json={"values": {"model": ""}})
    assert "model" not in tomllib.loads(path.read_text(encoding="utf-8"))["providers"]["video_gen"]["wan"]
    assert (
        client.put("/api/v2/settings/providers/video_gen/wan", json={"values": {"nope": "1"}}).status_code
        == 422
    )
    assert client.put("/api/v2/settings/providers/video_gen/none", json={"values": {}}).status_code == 404


def test_defaults_and_proxy(tmp_path):
    client, path, settings = _client(tmp_path)
    r = client.put("/api/v2/settings/defaults", json={"kind": "video_gen", "provider": "wan"})
    assert r.status_code == 200 and r.json()["defaults"]["video_gen"] == "wan"
    assert settings.defaults["video_gen"] == "wan"
    assert client.put("/api/v2/settings/app", json={"proxy": "ftp://x"}).status_code == 422
    r = client.put("/api/v2/settings/app", json={"proxy": "http://127.0.0.1:7890"})
    assert r.json()["proxy"] == "http://127.0.0.1:7890" and settings.proxy == "http://127.0.0.1:7890"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["app"]["proxy"] == "http://127.0.0.1:7890" and data["defaults"]["video_gen"] == "wan"
    client.put("/api/v2/settings/app", json={"proxy": ""})
    assert "proxy" not in tomllib.loads(path.read_text(encoding="utf-8"))["app"]


def test_test_connection_reports_missing_key(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOTWRIGHT_VIDEO_GEN_WAN_API_KEY", raising=False)
    client, _, _ = _client(tmp_path)
    r = client.post("/api/v2/settings/providers/video_gen/wan/test")
    assert r.status_code == 200 and r.json()["ok"] is False


def test_every_builtin_provider_has_setup_guide(client):
    """服务设置页的「怎么开通」说明：每个内置服务都要写，新加服务时别漏了。"""
    from shotwright.providers import registry, setup_guide

    registry.specs()
    builtin = [
        key
        for key, entry in registry._ENTRIES.items()
        if getattr(entry.factory, "__module__", "").startswith("shotwright.")
    ]
    assert builtin
    assert [f"{kind}.{pid}" for kind, pid in builtin if not all(setup_guide.guide(kind, pid))] == []
    providers = client.get("/api/v2/providers").json()
    seedance = next(item for item in providers["video_gen"] if item["id"] == "seedance")
    assert seedance["setup"] and seedance["shares_key_with"] == [{"kind": "llm", "id": "doubao"}]
    assert setup_guide.guide("nope", "nope") == ("", "")
