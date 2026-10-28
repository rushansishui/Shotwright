"""剪辑页：识别原片里的人声，按文字剪视频（去口误、去停顿），生成原声镜头。

流程：
1. 原片作为普通素材上传；
2. :func:`start` 在后台线程里抽出音轨交给语音识别服务，结果（逐词时间戳 + 分句）存在
   ``cache/transcripts/<素材 id>.json``；
3. 用户在剪辑页勾掉不要的词（口误、语气词、重说的句子），可以选择压缩长停顿；
4. :func:`plan_ranges` 算出要保留的时间段，:func:`build_shots` 把每段变成一个引用原片入出点的
   原声镜头（``Shot.speech = "original"``）：不再配音，字幕用识别出的逐词时间对齐。
"""

from __future__ import annotations

import logging
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from shotwright.config import Settings
from shotwright.core.models import Project, Shot, ShotVisual
from shotwright.core.store import ProjectStore, read_json, write_json
from shotwright.media.ffmpeg import MediaError, detect_scenes, probe, run_ffmpeg
from shotwright.providers import registry
from shotwright.providers.base import ASRProvider, LLMProvider, ProviderError, Transcript

log = logging.getLogger(__name__)

PAD = 0.08
"""剪掉一个词时，前后各留出的余量（秒），避免切掉相邻词的尾音。"""

PAUSE_KEEP = 0.25
"""压缩长停顿时，停顿两边各保留多少秒，听起来不会太赶。"""

MIN_RANGE = 0.3
"""短于这个秒数的保留片段直接丢掉（剪完只剩一下呼吸声之类）。"""

SHOT_TARGET = 8.0
"""一段连续保留的内容较长时，在句末切成多个镜头，每个大约这么长，方便单独调整。"""

SHOT_MAX = 20.0

BIG_GAP = 3.0
"""剪掉超过这么长的一段（例如删掉一整段重录）时另起一个镜头。"""

FILLERS = {
    "zh": {"嗯", "啊", "呃", "额", "哦", "唔", "嗯嗯", "啊啊", "呃呃", "emm", "嗯哼"},
    "en": {"um", "uh", "erm", "er", "hmm", "mm", "uhm", "ah"},
    "pt": {"é", "hã", "ahn", "hum", "uh", "eh"},
    "es": {"eh", "em", "mmm", "uh", "ehh"},
}
_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")

# 识别中的任务：(项目 id, 素材 id) → 进度、阶段、开始时间。
_running: dict[tuple[str, str], dict] = {}
_running_lock = threading.Lock()


@dataclass
class Word:
    text: str
    start: float
    end: float


def transcript_path(store: ProjectStore, project_id: str, asset_id: str) -> Path:
    return store.cache_dir(project_id, "transcripts") / f"{asset_id}.json"


def load(store: ProjectStore, project_id: str, asset_id: str) -> dict:
    """识别状态：``{"state": "none" | "running" | "done" | "failed", ...}``，完成时带 words / sentences。"""
    key = (project_id, asset_id)
    with _running_lock:
        if key in _running:
            info = _running[key]
            return {
                "state": "running",
                "progress": round(info["progress"], 3),
                "stage": info["stage"],
                "elapsed": round(time.time() - info["since"], 1),
            }
    data = read_json(transcript_path(store, project_id, asset_id))
    if not data:
        return {"state": "none"}
    if data.get("state") == "running":
        return {"state": "failed", "error": "识别被中断（服务重启过），请重新识别"}
    return data


def words_of(data: dict) -> list[Word]:
    return [Word(str(w[0]), float(w[1]), float(w[2])) for w in data.get("words") or [] if len(w) == 3]


def join_words(words: list[str]) -> str:
    """把识别出的词拼回一句话：中日韩之间不加空格，拼音文字之间加空格。"""
    text = ""
    for word in words:
        if text and not (_CJK.search(word[:1]) or _CJK.search(text[-1:]) or not word[:1].isalnum()):
            text += " "
        text += word
    return text


def _extract_audio(source: Path, output: Path) -> None:
    """抽出单声道 16kHz 音轨（32kbps mp3）：识别够用，30 分钟约 7MB，云端接口也能收。"""
    run_ffmpeg(
        ["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k", str(output)],
        timeout=900,
    )


def start(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    asset_id: str,
    *,
    provider_id: str | None = None,
    language: str | None = None,
) -> dict:
    """在后台识别一段原片；已经在识别时直接返回当前状态。"""
    asset = project.assets.get(asset_id)
    if asset is None:
        raise KeyError(asset_id)
    if asset.kind not in ("video", "audio"):
        raise ProviderError("只能识别视频或音频素材")
    asr: ASRProvider = registry.create("asr", provider_id, settings)  # type: ignore[assignment]
    source = store.asset_path(project, asset_id)
    key = (project.id, asset_id)
    with _running_lock:
        if key in _running:
            return {"state": "running", "progress": _running[key]["progress"], "stage": _running[key]["stage"]}
        _running[key] = {"progress": 0.0, "stage": "extract", "since": time.time()}
    path = transcript_path(store, project.id, asset_id)
    lang = language or project.language

    def progress(value: float, stage: str = "recognize") -> None:
        with _running_lock:
            if key in _running:
                _running[key].update(progress=max(0.0, min(1.0, value)), stage=stage)

    def run() -> None:
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="shotwright-asr-") as temp:
                audio = Path(temp) / "speech.mp3"
                _extract_audio(source, audio)
                progress(0.0, "recognize")
                result: Transcript = asr.transcribe(audio, language=lang, progress=progress)
            write_json(
                path,
                {
                    "state": "done",
                    "language": result.language or lang,
                    "provider": provider_id or settings.default_provider("asr"),
                    "duration": asset.duration,
                    "words": [[w.text, w.start, w.end] for w in result.words],
                    "sentences": [list(s) for s in result.sentences],
                    "seconds": round(time.monotonic() - started, 1),
                },
            )
            log.info("素材 %s 识别完成：%d 个词，用时 %.0f 秒", asset_id, len(result.words), time.monotonic() - started)
            if language == "auto" and result.language:
                _adopt_language(store, project.id, result.language)
        except (ProviderError, MediaError) as exc:
            write_json(path, {"state": "failed", "error": str(exc)})
        except Exception as exc:  # 识别库内部错误也要落盘，否则页面一直显示识别中
            log.exception("识别素材 %s 失败", asset_id)
            write_json(path, {"state": "failed", "error": f"识别失败：{exc}"})
        finally:
            with _running_lock:
                _running.pop(key, None)

    threading.Thread(target=run, name=f"asr-{asset_id}", daemon=True).start()
    return {"state": "running", "progress": 0.0, "stage": "extract", "elapsed": 0.0}


def _adopt_language(store: ProjectStore, project_id: str, detected: str) -> None:
    """自动识别语言时，把项目语言改成识别出的语言（字幕字体、翻译版本都按它来）。"""
    from shotwright.core import languages
    from shotwright.services.localize import apply_language

    code = languages.normalize(detected)
    with store.lock(project_id):
        latest = store.load(project_id)
        if languages.normalize(latest.language) != code:
            apply_language(latest, code)
            store.save(latest)


# ---- 按文字剪 ------------------------------------------------------------------


def _bare(text: str) -> str:
    return _PUNCT.sub("", text).lower()


def filler_indexes(words: list[Word], language: str) -> list[int]:
    """语气词（嗯、啊、um、uh……）的下标。"""
    lang = (language or "").split("-")[0].lower()
    fillers = FILLERS.get(lang, set()) | FILLERS["en"] if lang != "zh" else FILLERS["zh"]
    return [i for i, w in enumerate(words) if _bare(w.text) and _bare(w.text) in fillers]


def plan_ranges(
    words: list[Word],
    total: float,
    removed: set[int],
    *,
    max_pause: float | None = None,
) -> list[tuple[float, float]]:
    """算出剪完后要保留的时间段（原片里的秒数）。

    - 删掉的词：从前一个保留词的结尾剪到后一个保留词的开头（两边留 ``PAD``）；
    - ``max_pause`` 不为空时，保留词之间（以及开头、结尾）超过这么长的停顿压缩到两边各留 ``PAUSE_KEEP``；
      为空时没有说话的画面（例如商品展示）原样保留。
    """
    cuts: list[tuple[float, float]] = []
    count = len(words)
    index = 0
    while index < count:
        if index not in removed:
            index += 1
            continue
        first = index
        while index < count and index in removed:
            index += 1
        last = index - 1
        before = words[first - 1].end if first > 0 else None
        after = words[index].start if index < count else None
        begin = words[first].start if before is None else min(before + PAD, words[first].start)
        end = words[last].end if after is None else max(after - PAD, words[last].end)
        if before is None and max_pause is not None:
            begin = 0.0
        if after is None and max_pause is not None:
            end = total
        cuts.append((max(0.0, begin), min(total, end)))

    if max_pause is not None:
        kept = [w for i, w in enumerate(words) if i not in removed]
        if kept:
            edges = [(0.0, kept[0].start), *((a.end, b.start) for a, b in zip(kept, kept[1:], strict=False)), (kept[-1].end, total)]
            for position, (a, b) in enumerate(edges):
                if b - a <= max_pause:
                    continue
                left = a + (0.0 if position == 0 else PAUSE_KEEP)
                right = b - (0.0 if position == len(edges) - 1 else PAUSE_KEEP)
                if right > left:
                    cuts.append((left, right))
        elif removed:
            return []

    cuts.sort()
    merged: list[list[float]] = []
    for begin, end in cuts:
        if merged and begin <= merged[-1][1] + 1e-3:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([begin, end])
    ranges: list[tuple[float, float]] = []
    cursor = 0.0
    for begin, end in merged:
        if begin - cursor >= MIN_RANGE:
            ranges.append((round(cursor, 3), round(begin, 3)))
        cursor = max(cursor, end)
    if total - cursor >= MIN_RANGE:
        ranges.append((round(cursor, 3), round(total, 3)))
    return ranges


def _split_points(
    begin: float, end: float, words: list[Word], sentences: list[tuple[float, float]], scenes: list[float]
) -> list[float]:
    """一段保留内容里再切开的位置：画面切换处，以及较长时的句末（找不到句末就在词间最大的停顿处）。"""
    points = sorted(p for p in scenes if begin + 1.0 < p < end - 1.0)
    bounds = [begin, *points, end]
    result: list[float] = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        if a > begin:
            result.append(a)
        cursor = a
        while b - cursor > SHOT_TARGET * 1.6:
            target = cursor + SHOT_TARGET
            limit = min(cursor + SHOT_MAX, b - 1.0)
            ends = [s_end for _, s_end in sentences if target <= s_end <= limit]
            if not ends:
                gaps = [
                    ((w2.start - w1.end), (w1.end + w2.start) / 2)
                    for w1, w2 in zip(words, words[1:], strict=False)
                    if cursor + 2.0 <= w1.end and w2.start <= limit
                ]
                ends = [max(gaps)[1]] if gaps else [min(limit, cursor + SHOT_MAX)]
            cut = round(ends[0], 3)
            if cut <= cursor + 1.0:
                break
            result.append(cut)
            cursor = cut
    return result


@dataclass
class CutResult:
    shot_ids: list[str]
    kept_seconds: float
    source_seconds: float


def build_shots(
    store: ProjectStore,
    project: Project,
    asset_id: str,
    *,
    removed: set[int],
    max_pause: float | None,
    split_scenes: bool = True,
    texts: dict[int, str] | None = None,
) -> CutResult:
    """按剪辑结果生成原声镜头，替换项目里现有的镜头。``texts`` 是用户改过错字的词（下标 → 新文字）。"""
    data = load(store, project.id, asset_id)
    if data.get("state") != "done":
        raise ProviderError("这段原片还没有识别完成")
    asset = project.assets[asset_id]
    source = store.asset_path(project, asset_id)
    total = asset.duration or float(data.get("duration") or 0.0)
    if not total:
        total = float(probe(source).duration or 0.0)
    words = words_of(data)
    for index, text in (texts or {}).items():
        if 0 <= index < len(words) and text.strip():
            words[index] = Word(text.strip(), words[index].start, words[index].end)
    sentences = [(float(a), float(b)) for a, b in data.get("sentences") or []]
    ranges = plan_ranges(words, total, removed, max_pause=max_pause)
    if not ranges:
        raise ProviderError("全部内容都被剪掉了，至少保留一段")

    scenes: list[float] = []
    if split_scenes and asset.kind == "video":
        try:
            scenes = detect_scenes(source)
        except MediaError as exc:
            log.warning("原片切镜检测失败，只按句子切：%s", exc)
    has_audio = True
    try:
        has_audio = probe(source).has_audio
    except MediaError:
        pass

    kept_words = [w for i, w in enumerate(words) if i not in removed]
    # 先把每段保留内容在画面切换处、较长时在句末切开（这些是镜头的分界）；
    # 再把被剪掉的口误、停顿隔开的相邻片段并进同一个镜头，剪掉的部分记在 skips 里，
    # 一个镜头大约 SHOT_TARGET 秒，不会因为删了一个"嗯"就多出一个镜头。
    pieces: list[tuple[float, float, bool]] = []
    for begin, end in ranges:
        inside = [w for w in kept_words if begin <= (w.start + w.end) / 2 <= end]
        bounds = [begin, *_split_points(begin, end, inside, sentences, scenes), end]
        for position, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
            if b - a >= 0.1:
                pieces.append((a, b, position > 0))
    groups: list[list[tuple[float, float]]] = []
    for a, b, boundary in pieces:
        if groups and not boundary:
            current = groups[-1]
            kept = sum(y - x for x, y in current)
            gap = a - current[-1][1]
            if kept < SHOT_TARGET and gap <= BIG_GAP and kept + (b - a) <= SHOT_MAX:
                current.append((a, b))
                continue
        groups.append([(a, b)])

    shots: list[Shot] = []
    for group in groups:
        middle = [(w.start + w.end) / 2 for w in kept_words]
        text = join_words(
            [w.text for w, m in zip(kept_words, middle, strict=True) if any(a <= m <= b for a, b in group)]
        )
        skips = [(round(x[1], 3), round(y[0], 3)) for x, y in zip(group, group[1:], strict=False) if y[0] - x[1] > 0.001]
        shots.append(
            Shot(
                text=text,
                duration=round(min(120.0, sum(b - a for a, b in group)), 3),
                speech="original",
                visual=ShotVisual(
                    mode="upload",
                    candidates=[asset_id],
                    selected=asset_id,
                    edit_asset=asset_id,
                    trim_start=round(group[0][0], 3),
                    trim_end=round(group[-1][1], 3),
                    skips=skips,
                    source_volume=1.0 if has_audio else 0.0,
                ),
            )
        )
    project.shots = shots
    if texts:
        # 改过的错字记回识别结果，重新打开剪辑页、渲染字幕都用改后的文字。
        stored = data.get("words") or []
        for index, text in texts.items():
            if 0 <= index < len(stored) and text.strip():
                stored[index][0] = text.strip()
        write_json(transcript_path(store, project.id, asset_id), data)
    kept = sum(b - a for a, b in ranges)
    log.info("剪辑：原片 %.1f 秒，保留 %.1f 秒，生成 %d 个镜头", total, kept, len(shots))
    return CutResult([s.id for s in shots], round(kept, 3), round(total, 3))


def shot_words(store: ProjectStore, project: Project, shot: Shot) -> list[tuple[str, float, float]]:
    """原声镜头的逐词时间（相对镜头开头），给字幕对齐用；没有识别结果时返回空。"""
    visual = shot.visual
    asset_id = visual.selected
    if shot.speech != "original" or not asset_id or visual.edit_asset != asset_id:
        return []
    data = read_json(transcript_path(store, project.id, asset_id))
    if not data or data.get("state") != "done":
        return []
    begin = visual.trim_start
    end = visual.trim_end if visual.trim_end is not None else begin + (shot.duration or 0.0)
    result = []
    for word in words_of(data):
        middle = (word.start + word.end) / 2
        if not begin <= middle < end:
            continue
        at = visual.to_shot_time(begin, end, middle)
        if at is None:  # 词落在剪掉的片段里
            continue
        start = visual.to_shot_time(begin, end, max(begin, word.start))
        finish = visual.to_shot_time(begin, end, min(end, word.end))
        start = at if start is None else start
        finish = at if finish is None else finish
        result.append((word.text, round(max(0.0, start), 3), round(max(start, finish), 3)))
    return result


# ---- AI 去口误 ------------------------------------------------------------------


def suggest_retakes(llm: LLMProvider, data: dict) -> list[int]:
    """让大模型找出重说、说错后改口、说了一半放弃的句子，返回这些句子里所有词的下标。"""
    from shotwright.services.storyboard import _extract_json

    words = words_of(data)
    sentences = [(float(a), float(b)) for a, b in data.get("sentences") or []]
    if not words or not sentences:
        return []
    groups: list[list[int]] = []
    for a, b in sentences:
        members = [i for i, w in enumerate(words) if a - 0.05 <= (w.start + w.end) / 2 <= b + 0.05]
        if members and (not groups or members[0] > groups[-1][-1]):
            groups.append(members)
    if not groups:
        return []
    numbered = [{"n": n + 1, "text": join_words([words[i].text for i in g])} for n, g in enumerate(groups)][:400]
    import json

    messages = [
        {
            "role": "system",
            "content": (
                "你是短视频剪辑师。下面是口播视频逐句的语音识别结果。找出应该剪掉的句子："
                "1) 说错后马上重说的那一遍里，前面说错、没说完的那一遍（保留最后完整的一遍）；"
                "2) 说了一半放弃的句子；3) 和视频内容无关的话（例如“再来一遍”“这段不要”“好，开始”）。"
                "不要剪正常的内容，拿不准就保留。"
                '只返回 JSON：{"remove": [句子编号], "reasons": {"编号": "原因"}}'
            ),
        },
        {"role": "user", "content": json.dumps(numbered, ensure_ascii=False)},
    ]
    reply = _extract_json(llm.complete(messages, json_mode=True))
    result: list[int] = []
    for value in reply.get("remove") or []:
        try:
            n = int(value) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= n < len(groups):
            result += groups[n]
    return sorted(set(result))
