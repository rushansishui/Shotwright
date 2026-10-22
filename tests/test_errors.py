"""服务端错误提示的英文版。"""

from concurrent.futures import Executor, Future

import pytest
from fastapi.testclient import TestClient

from shotwright.api.app import create_app
from shotwright.api.jobs import JobManager
from shotwright.core.store import ProjectStore


class _InlineExecutor(Executor):
    def submit(self, fn, *args, **kwargs):
        future: Future = Future()
        future.set_result(fn(*args, **kwargs))
        return future


@pytest.fixture
def client(settings):
    store = ProjectStore(settings.storage_dir)
    app = create_app(settings, job_manager=JobManager(store, settings, executor=_InlineExecutor()))
    with TestClient(app) as test_client:
        yield test_client


def test_errors_translate_for_english_clients(client):
    zh = client.get("/api/v2/projects/doesnotexist", headers={"Accept-Language": "zh-CN"})
    en = client.get("/api/v2/projects/doesnotexist", headers={"Accept-Language": "en-US"})
    assert zh.json()["detail"] == "项目不存在"
    assert en.json()["detail"] == "Project not found"


def test_errors_stay_chinese_when_language_is_zh_first(client):
    r = client.get(
        "/api/v2/projects/doesnotexist", headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    )
    assert r.json()["detail"] == "项目不存在"
