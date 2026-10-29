"""进程级的并发闸门：限制同时在跑的收费生成和本地识别。

一键生成是按项目各起一个后台线程（见 ``services/batch.py``），语音识别是每个素材一个线程，
渲染队列本身就是单线程。没有总量控制时，同时开几个项目的一键生成就会各自猛打同一个
（收费）接口，本机识别也会把 CPU 占满。这里给一个统一的信号量：

* ``AI``：调用 AI 视频 / 图片服务的并发数，默认 4；
* ``ASR``：本地语音识别的并发数，默认 1（faster-whisper 是 CPU 密集的）。

数量可以用环境变量或在 ``config.toml`` 的 ``[app]`` 里改（见 :func:`configure`）。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

DEFAULT_AI_CONCURRENCY = 4
DEFAULT_ASR_CONCURRENCY = 1

_GUARD = threading.Lock()
_SEMAPHORES: dict[str, tuple[int, threading.BoundedSemaphore]] = {}


def _semaphore(name: str, size: int) -> threading.BoundedSemaphore:
    """按名字取信号量；数量变了就换一个新的（启动时配置一次，之后不再变）。"""
    size = max(1, int(size))
    with _GUARD:
        current = _SEMAPHORES.get(name)
        if current is not None and current[0] == size:
            return current[1]
        created = (size, threading.BoundedSemaphore(size))
        _SEMAPHORES[name] = created
        return created[1]


@contextmanager
def limit(name: str, size: int) -> Iterator[None]:
    """在 ``size`` 个并发以内执行一块代码；排队时阻塞当前线程。"""
    semaphore = _semaphore(name, size)
    semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()


def configure(*, ai: int | None = None, asr: int | None = None) -> None:
    """按配置设置并发数（应用启动时调用一次）。"""
    if ai is not None:
        _semaphore("ai", ai)
    if asr is not None:
        _semaphore("asr", asr)


def stats() -> dict[str, int]:
    """当前的并发上限，供日志和测试查看。"""
    with _GUARD:
        return {name: size for name, (size, _semaphore) in _SEMAPHORES.items()}


def reset() -> None:
    """测试用：清空所有信号量。"""
    with _GUARD:
        _SEMAPHORES.clear()
