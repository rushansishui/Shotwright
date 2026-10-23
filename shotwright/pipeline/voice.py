"""配音阶段：把镜头文案合成为音频，按指纹缓存。

无文案的镜头生成对应时长的静音音频。
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from pathlib import Path

from shotwright.config import Settings
from shotwright.core.fingerprint import fingerprint
from shotwright.core.models import Project, Shot
from shotwright.core.store import ProjectStore, read_json, write_json
from shotwright.media.ffmpeg import run_ffmpeg
from shotwright.providers import registry
from shotwright.providers.base import ProviderError, TTSProvider

from .render_types import AUDIO_ARGS, RenderError, StageRecord


def synthesize_shot(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    *,
    stages: list[StageRecord],
    cancel: threading.Event,
) -> tuple[str, Path, float, list[tuple[str, float, float]]]:
    """为单个镜头生成（或从缓存取回）配音文件。

    返回 (指纹, 音频路径, 时长秒, 逐词时间戳列表)。
    """
    voice = project.voice_for(shot)
    text = shot.text.strip()
    provider_id = voice.provider if text else "silence"
    fp = fingerprint("voice", text, voice.model_dump(), shot.duration if not text else None)
    cache = store.cache_dir(project.id, "voice")
    meta_path = cache / f"{fp}.json"
    meta = read_json(meta_path)
    if meta and (cache / meta.get("file", "")).is_file():
        stages.append(StageRecord(f"{shot.id}:voice", "voice", fp, True, 0.0))
        words = [(str(w[0]), float(w[1]), float(w[2])) for w in meta.get("words") or [] if len(w) == 3]
        return fp, cache / meta["file"], float(meta["duration"]), words

    started = time.monotonic()
    words: list[tuple[str, float, float]] = []
    with tempfile.TemporaryDirectory(prefix="shotwright-voice-") as temp:
        if not text:
            seconds = shot.duration or 2.0
            path = Path(temp) / "voice.m4a"
            run_ffmpeg(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-t",
                    f"{seconds:.3f}",
                    *AUDIO_ARGS,
                    str(path),
                ],
                cancel=cancel,
            )
            duration = seconds
        else:
            try:
                tts: TTSProvider = registry.create("tts", provider_id, settings)  # type: ignore[assignment]
                speech = tts.synthesize(
                    text,
                    voice=voice.voice,
                    rate=voice.rate,
                    volume=voice.volume,
                    output=Path(temp) / "voice",
                )
            except ProviderError as exc:
                index = project.shots.index(shot) + 1
                raise RenderError(f"镜头 {index} 配音失败：{exc}") from exc
            path, duration = speech.path, speech.duration
            words = [(word.text, word.start, word.end) for word in speech.words]
        filename = f"{fp}{path.suffix}"
        shutil.move(str(path), cache / filename)
    write_json(
        meta_path,
        {
            "file": filename,
            "duration": round(duration, 3),
            "provider": provider_id,
            "words": [list(word) for word in words],
        },
    )
    stages.append(
        StageRecord(f"{shot.id}:voice", "voice", fp, False, round(time.monotonic() - started, 3))
    )
    return fp, cache / filename, round(duration, 3), words
