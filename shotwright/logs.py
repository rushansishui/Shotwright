"""日志：写到 ``<存储目录>/../logs/shotwright.log``，每天切分一次，保留 14 天。

收费任务的提交、任务 id 和结果都会记下来，方便到服务商控制台核对扣费。
所有日志都经过脱敏：配置和环境变量里的 API Key 会被替换成 ``***``。
"""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from shotwright.config import Settings

LOGGER_NAME = "shotwright"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class _RedactSecrets(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        # 长的先替换，避免一个 Key 是另一个的前缀时漏掉。
        self.secrets = sorted({s for s in secrets if len(s) >= 6}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.secrets:
            return True
        message = record.getMessage()
        redacted = message
        for secret in self.secrets:
            redacted = redacted.replace(secret, "***")
        if record.exc_info and record.exc_info[1] is not None:
            # 异常文本在格式化时才生成，先格式化好再脱敏。
            text = logging.Formatter().formatException(record.exc_info)
            for secret in self.secrets:
                text = text.replace(secret, "***")
            record.exc_text = text
            record.exc_info = None
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


def _secrets(settings: Settings) -> list[str]:
    values = [
        value
        for entries in settings.providers.values()
        for options in entries.values()
        for key, value in options.items()
        if "key" in key.lower() or "token" in key.lower() or "secret" in key.lower()
    ]
    values += [
        v for k, v in os.environ.items() if k.startswith("SHOTWRIGHT_") and k.endswith(("_KEY", "_TOKEN"))
    ]
    return values


def log_dir(settings: Settings) -> Path:
    return Path(settings.storage_dir).resolve().parent / "logs"


_POLLING = re.compile(
    r'"GET /api/v2/(projects/[^/ ]+/(transcripts/[^/ ]+|in-progress)|renders/[^/ ]+) HTTP/[\d.]+" 200'
)


class _QuietPolling(logging.Filter):
    """页面每隔一两秒查询一次进度（识别、生成、渲染），成功的查询不写进访问日志，免得刷屏。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _POLLING.search(record.getMessage())


def quiet_polling_access_log() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _QuietPolling) for f in access.filters):
        access.addFilter(_QuietPolling())


def setup_logging(settings: Settings, *, console: bool = True) -> Path:
    """配置 ``shotwright`` 日志。可以重复调用，不会重复添加输出。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        if getattr(handler, "_shotwright", False):
            logger.removeHandler(handler)
            handler.close()
    directory = log_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "shotwright.log"
    redact = _RedactSecrets(_secrets(settings))
    handlers: list[logging.Handler] = [
        TimedRotatingFileHandler(path, when="midnight", backupCount=14, encoding="utf-8", delay=True)
    ]
    if console:
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.addFilter(redact)
        handler._shotwright = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.propagate = False
    quiet_polling_access_log()
    return path
