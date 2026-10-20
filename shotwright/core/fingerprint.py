"""阶段指纹：输入不变，产出就能复用。

指纹是对\"决定产出的全部输入\"做的 SHA-256。任何一个输入变化，指纹就变，
对应阶段就会重做；输入不变则直接命中缓存。渲染器实现变化时提升
:data:`RENDERER_VERSION`，让旧缓存整体失效。
"""

from __future__ import annotations

import hashlib
import json

RENDERER_VERSION = "shotwright-render-1"


def fingerprint(*parts: object) -> str:
    payload = json.dumps(
        [RENDERER_VERSION, *parts],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
