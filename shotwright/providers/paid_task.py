"""收费异步任务的公共逻辑：提交、轮询、下载。

语义参考 MoneyPrinterTurbo ``app/services/video_providers/client.py`` 与
``polling.py``（MIT），用 httpx 重写：

* **提交绝不自动重试。** 超时、5xx、重定向或响应无法解析时，远端可能已经创建并
  计费，一律抛 :class:`UnconfirmedTaskError`，由用户决定是否重新提交。只有明确的
  4xx 才视为"远端拒绝、未扣费"。
* **轮询可以重试。** 查询状态不会产生费用，网络抖动和 429 / 5xx 会按线性退避重试，
  超过次数或总时长后抛 :class:`UnconfirmedTaskError`，并带上远端任务 id 方便排查。
* 所有错误信息都会抹掉 API Key。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from shotwright.providers.base import ProviderError, UnconfirmedTaskError

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
MAX_DOWNLOAD = 500 * 1024 * 1024
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
    (b"BM", "image/bmp"),
)


def sniff_image_type(data: bytes) -> str | None:
    """按文件头判断图片类型；扩展名可能和内容不一致。"""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in _IMAGE_MAGIC:
        if data.startswith(magic):
            return mime
    return None


def redact(value: object, secret: str) -> str:
    text = str(value)
    return text.replace(secret, "***") if secret else text


def rejection_message(label: str, response: httpx.Response, secret: str, model: str = "") -> str:
    """4xx 拒绝的提示；模型不存在或未开通时告诉用户怎么处理。"""
    detail = error_detail(response, secret)
    message = f"{label} 拒绝了请求：HTTP {response.status_code}，{detail}"
    lowered = detail.lower()
    if response.status_code == 404 or "does not exist" in lowered or "modelnotopen" in lowered:
        target = f"模型 {model} " if model else "该模型"
        message += f"。请先在火山方舟控制台的「开通管理」里开通{target}，或在 config.toml 中换成已开通的模型"
    return message


def error_detail(response: httpx.Response, secret: str) -> str:
    try:
        body = response.json()
    except ValueError:
        return redact(response.text[:300], secret)
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or error.get("code") or error
    else:
        message = error or body
    return redact(str(message)[:300], secret)


def submit(
    client: httpx.Client,
    url: str,
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, str],
    label: str,
    secret: str,
    task_id_field: str = "id",
) -> str:
    """提交收费任务，返回远端任务 id。不重试。"""
    try:
        response = client.post(url, json=dict(payload), headers=dict(headers), follow_redirects=False)
    except httpx.HTTPError as exc:
        raise UnconfirmedTaskError(
            f"{label} 提交没有收到响应，远端可能已经创建任务并扣费，请到服务商控制台确认后再决定是否重试"
            f"（{type(exc).__name__}）"
        ) from exc
    status = response.status_code
    if 300 <= status < 400 or status >= 500:
        raise UnconfirmedTaskError(
            f"{label} 提交返回 HTTP {status}，远端可能已经创建任务并扣费，请到服务商控制台确认"
        )
    if status >= 400:
        raise ProviderError(rejection_message(label, response, secret, str(payload.get("model") or "")))
    try:
        body = response.json()
    except ValueError as exc:
        raise UnconfirmedTaskError(f"{label} 提交的响应无法解析，远端可能已经创建任务") from exc
    task_id = str((body or {}).get(task_id_field) or "").strip() if isinstance(body, dict) else ""
    if not task_id:
        raise UnconfirmedTaskError(f"{label} 接受了请求但没有返回任务 id")
    return task_id


@dataclass(frozen=True)
class PollSettings:
    label: str
    interval: float = 5.0
    timeout: float = 1800.0
    max_retries: int = 5
    success: frozenset[str] = frozenset({"succeeded"})
    active: frozenset[str] = frozenset({"queued", "running"})
    failure: frozenset[str] = frozenset({"failed", "cancelled", "canceled", "expired"})
    status_of: Callable[[dict], str] = field(default=lambda body: str(body.get("status") or ""))
    error_of: Callable[[dict], Any] = field(default=lambda body: body.get("error"))


def poll(
    client: httpx.Client,
    url: str,
    *,
    task_id: str,
    headers: Mapping[str, str],
    secret: str,
    settings: PollSettings,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """轮询同一个任务直到成功；失败抛 ProviderError，无法确认抛 UnconfirmedTaskError。"""
    label = settings.label
    deadline = clock() + settings.timeout
    failures = 0
    while True:
        if clock() >= deadline:
            raise UnconfirmedTaskError(f"{label} 任务超过等待时间仍未完成，任务 id：{task_id}")
        try:
            response = client.get(url, headers=dict(headers), follow_redirects=False)
            if response.status_code in RETRYABLE_STATUS:
                raise httpx.HTTPStatusError("retryable", request=response.request, response=response)
            if response.status_code >= 300:
                raise UnconfirmedTaskError(
                    f"{label} 查询任务状态失败：HTTP {response.status_code}，任务 id：{task_id}"
                )
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except UnconfirmedTaskError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            failures += 1
            if failures > settings.max_retries:
                raise UnconfirmedTaskError(
                    f"{label} 多次查询任务状态失败，任务可能仍在远端运行，任务 id：{task_id}"
                ) from exc
            sleep(min(float(failures), max(deadline - clock(), 0)))
            continue
        failures = 0
        status = settings.status_of(body).strip().lower()
        if status in settings.success:
            return body
        if status in settings.failure:
            detail = redact(settings.error_of(body) or "", secret)[:300]
            raise ProviderError(f"{label} 任务失败（{status}）：{detail}，任务 id：{task_id}")
        if status not in settings.active:
            raise UnconfirmedTaskError(f"{label} 返回了未知状态 {status!r}，任务 id：{task_id}")
        sleep(min(settings.interval, max(deadline - clock(), 0)))


def download(client: httpx.Client, url: str, output: Path, *, label: str, task_id: str = "") -> Path:
    """下载生成结果。远端已经成功扣费，失败时在消息里保留任务 id。"""
    if not url.startswith(("https://", "http://")):
        raise ProviderError(f"{label} 没有返回可下载的地址")
    received = 0
    try:
        with client.stream("GET", url, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise ProviderError(f"{label} 结果下载失败：HTTP {response.status_code}，任务 id：{task_id}")
            with output.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 256):
                    received += len(chunk)
                    if received > MAX_DOWNLOAD:
                        raise ProviderError(f"{label} 结果文件过大")
                    handle.write(chunk)
    except httpx.HTTPError as exc:
        output.unlink(missing_ok=True)
        raise ProviderError(f"{label} 结果下载失败（{type(exc).__name__}），任务 id：{task_id}") from exc
    except ProviderError:
        output.unlink(missing_ok=True)
        raise
    if received == 0:
        output.unlink(missing_ok=True)
        raise ProviderError(f"{label} 返回了空文件，任务 id：{task_id}")
    return output
