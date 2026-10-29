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
from shotwright.services import pronounce
from shotwright.services import speech as marks

from .render_types import AUDIO_ARGS, RenderError, StageRecord


def _synthesize_marked(
    tts: TTSProvider, text: str, voice, work: Path, cancel: threading.Event
) -> tuple[Path, float, list[tuple[str, float, float]]]:
    """有停顿或强调标注的文案：分段合成，段前插入静音，再拼成一条音频。

    强调的段落语速稍慢、音量稍高；逐词时间戳按每段的起点平移后合并。
    """
    inputs: list[str] = []
    labels: list[str] = []
    words: list[tuple[str, float, float]] = []
    cursor = 0.0
    for index, segment in enumerate(marks.segments(text)):
        if segment.pause_before > 0:
            inputs += [
                "-f", "lavfi", "-t", f"{segment.pause_before:.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
            labels.append(f"[{len(labels)}:a]")
            cursor += segment.pause_before
        result = tts.synthesize(
            segment.text,
            voice=voice.voice,
            rate=min(2.0, voice.rate * (marks.EMPHASIS_RATE if segment.emphasis else 1.0)),
            volume=min(2.0, voice.volume * (marks.EMPHASIS_VOLUME if segment.emphasis else 1.0)),
            output=work / f"part{index}",
        )
        inputs += ["-i", str(result.path)]
        labels.append(f"[{len(labels)}:a]")
        words += [(word.text, word.start + cursor, word.end + cursor) for word in result.words]
        cursor += result.duration
    output = work / "voice.m4a"
    graph = "".join(f"{label}aresample=48000,aformat=channel_layouts=stereo[s{i}];" for i, label in enumerate(labels))
    graph += "".join(f"[s{i}]" for i in range(len(labels))) + f"concat=n={len(labels)}:v=0:a=1[out]"
    run_ffmpeg([*inputs, "-filter_complex", graph, "-map", "[out]", *AUDIO_ARGS, str(output)], cancel=cancel)
    return output, round(cursor, 3), words


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
    if shot.speech == "original":
        return _original_speech(store, project, shot, stages=stages, cancel=cancel)
    voice = project.voice_for(shot)
    # 读音纠正：易读错的词换成同音字再合成（字幕仍显示原文）；替换后的文字参与指纹，改了会重新配音。
    text = pronounce.spoken(project, shot.text.strip())
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
                if marks.has_marks(text):
                    path, duration, words = _synthesize_marked(tts, text, voice, Path(temp), cancel)
                else:
                    result = tts.synthesize(
                        text,
                        voice=voice.voice,
                        rate=voice.rate,
                        volume=voice.volume,
                        output=Path(temp) / "voice",
                    )
                    path, duration = result.path, result.duration
                    words = [(word.text, word.start, word.end) for word in result.words]
            except ProviderError as exc:
                index = project.shots.index(shot) + 1
                raise RenderError(f"镜头 {index} 配音失败：{exc}") from exc
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


def _original_speech(
    store: ProjectStore, project: Project, shot: Shot, *, stages: list[StageRecord], cancel: threading.Event
) -> tuple[str, Path, float, list[tuple[str, float, float]]]:
    """原声镜头：人声来自原片（按原声音量混进去），这里只生成镜头时长的静音轨，
    逐词时间取语音识别结果，字幕跟着原声走。"""
    from shotwright.services import transcribe

    seconds = shot.duration or 2.0
    words = transcribe.shot_words(store, project, shot)
    fp = fingerprint("voice-original", round(seconds, 3), words)
    cache = store.cache_dir(project.id, "voice")
    path = cache / f"{fp}.m4a"
    cached = path.is_file()
    started = time.monotonic()
    if not cached:
        with tempfile.TemporaryDirectory(prefix="shotwright-voice-") as temp:
            out = Path(temp) / "voice.m4a"
            run_ffmpeg(
                ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                 "-t", f"{seconds:.3f}", *AUDIO_ARGS, str(out)],
                cancel=cancel,
            )
            shutil.move(str(out), path)
    stages.append(StageRecord(f"{shot.id}:voice", "voice", fp, cached, round(time.monotonic() - started, 3)))
    return fp, path, round(seconds, 3), words


PREVIEW_LIMIT = 200
"""试听最多合成这么多字。"""


def preview_voice(store: ProjectStore, settings: Settings, project: Project, text: str) -> Path:
    """按项目的配音设置和读音纠正合成一句试听，结果按内容缓存。"""
    text = marks.plain(text).strip()[:PREVIEW_LIMIT]
    if not text:
        raise RenderError("试听的文字为空")
    voice = project.voice
    said = pronounce.spoken(project, text)
    fp = fingerprint("preview", said, voice.model_dump())
    cache = store.cache_dir(project.id, "preview")
    for existing in cache.glob(f"{fp}.*"):
        return existing
    tts: TTSProvider = registry.create("tts", voice.provider, settings)  # type: ignore[assignment]
    with tempfile.TemporaryDirectory(prefix="shotwright-preview-") as temp:
        result = tts.synthesize(said, voice=voice.voice, rate=voice.rate, volume=voice.volume, output=Path(temp) / "p")
        target = cache / f"{fp}{result.path.suffix}"
        shutil.move(str(result.path), target)
    return target
