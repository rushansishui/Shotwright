import logging

import httpx

from shotwright.logs import setup_logging
from shotwright.providers import paid_task


def test_log_file_records_paid_tasks_without_keys(settings, monkeypatch):
    settings.providers["llm"] = {"doubao": {"api_key": "ark-very-secret-key"}}
    path = setup_logging(settings, console=False)
    responses = iter([httpx.Response(200, json={"id": "cgt-42"})])
    client = httpx.Client(transport=httpx.MockTransport(lambda r: next(responses)))
    paid_task.submit(
        client,
        "https://x/tasks",
        {"model": "m"},
        headers={},
        label="Seedance",
        secret="ark-very-secret-key",
    )
    logging.getLogger("shotwright.test").error("leak? ark-very-secret-key")
    for handler in logging.getLogger("shotwright").handlers:
        handler.flush()
    text = path.read_text(encoding="utf-8")
    assert "cgt-42" in text and "Seedance" in text
    assert "ark-very-secret-key" not in text and "leak? ***" in text
