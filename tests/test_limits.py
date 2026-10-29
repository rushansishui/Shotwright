"""进程级并发上限。"""

import threading
import time

from shotwright.config import load_settings
from shotwright.core import limits


def test_limit_caps_concurrent_work():
    limits.reset()
    running, peak = 0, 0
    guard = threading.Lock()

    def work():
        nonlocal running, peak
        with limits.limit("test", 2):
            with guard:
                running += 1
                peak = max(peak, running)
            time.sleep(0.05)
            with guard:
                running -= 1

    threads = [threading.Thread(target=work) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak == 2
    limits.reset()


def test_concurrency_settings_from_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[app]\nai_concurrency = 2\nasr_concurrency = "x"\n', encoding="utf-8")
    settings = load_settings(path)
    assert settings.ai_concurrency == 2
    assert settings.asr_concurrency == 1  # 写错时用默认值
    limits.configure(ai=settings.ai_concurrency, asr=settings.asr_concurrency)
    assert limits.stats()["ai"] == 2
    limits.reset()


def test_waiting_threads_are_reported_as_queued():
    limits.reset()
    release = threading.Event()
    holder_in = threading.Event()
    waiter_ids: list[int] = []

    def holder():
        with limits.limit("q", 1):
            holder_in.set()
            release.wait(2)

    def waiter():
        waiter_ids.append(threading.get_ident())
        with limits.limit("q", 1):
            pass

    first = threading.Thread(target=holder)
    first.start()
    holder_in.wait(2)
    second = threading.Thread(target=waiter)
    second.start()
    for _ in range(100):
        if waiter_ids and limits.is_waiting(waiter_ids[0]):
            break
        time.sleep(0.01)
    assert limits.is_waiting(waiter_ids[0])
    assert not limits.is_waiting(first.ident)
    release.set()
    first.join()
    second.join()
    assert not limits.is_waiting(waiter_ids[0])
    limits.reset()
