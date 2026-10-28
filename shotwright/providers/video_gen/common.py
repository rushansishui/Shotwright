"""各家 AI 视频服务共用的部分：模型能力表、时长和分辨率对齐、图片编码、参考价格。

每个服务只需要写清楚三件事：自己的模型有哪些能力（:class:`VideoModel`）、请求怎么拼、
结果怎么取。其余的——时长取到模型支持的档位、草稿档降分辨率、参考图张数上限、
费用估算——都由这里和 ``services/visuals.py``、``services/cost.py`` 统一处理。

价格是写代码时查到的参考价（元 / 秒），会过时；用户可以在 config.toml 里用
``price`` 改成自己账户的实际单价。
"""

from __future__ import annotations

import base64
import math
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from shotwright.providers.base import ProviderError
from shotwright.providers.paid_task import sniff_image_type

Mode = Literal["text", "first_frame", "first_last", "references", "edit"]
AudioSupport = Literal["none", "optional", "always"]

RESOLUTION_ORDER = ("360p", "480p", "512p", "540p", "720p", "768p", "1080p", "2k", "4k")
"""分辨率从低到高；草稿档只会往低处降。"""


def resolution_rank(value: str) -> int:
    key = value.strip().lower()
    return RESOLUTION_ORDER.index(key) if key in RESOLUTION_ORDER else -1


USD_TO_CNY = 7.2
"""美元计价的模型换算成人民币的参考汇率（只用于估算费用）。"""


@dataclass(frozen=True)
class VideoModel:
    """一个视频模型能做什么。"""

    id: str
    modes: frozenset[str]
    """支持的输入方式：text 文生、first_frame 首帧、first_last 首尾帧、references 参考图、edit 改视频。"""
    min_duration: int = 4
    max_duration: int = 10
    durations: tuple[int, ...] = ()
    """只支持固定几档时长时列出（例如 6、10 秒）；为空表示 min～max 之间的整秒都行。"""
    resolutions: tuple[str, ...] = ("720p", "1080p")
    """从低到高排列，用小写（720p、1080p、4k）。"""
    max_references: int = 0
    audio: AudioSupport = "none"
    price: Mapping[str, float] = field(default_factory=dict)
    """各分辨率的参考单价（元 / 秒）。"""
    aspects: tuple[str, ...] = ("9:16", "16:9", "1:1")
    """文生视频支持的画面比例；图生视频按首帧比例。"""

    def snap(self, seconds: float, *, minimum: int | None = None, maximum: int | None = None) -> int:
        """把想要的时长取到模型支持的档位：向上取整，超过上限时取上限（多出的部分渲染时裁掉）。"""
        low = max(self.min_duration, minimum or self.min_duration)
        high = min(self.max_duration, maximum or self.max_duration)
        high = max(high, low)
        wanted = math.ceil(max(seconds, 0.1) - 1e-6)
        if self.durations:
            allowed = [d for d in self.durations if low <= d <= high] or list(self.durations)
            return next((d for d in sorted(allowed) if d >= wanted), max(allowed))
        return max(low, min(high, wanted))

    def longest(self, maximum: int | None = None) -> int:
        top = max(self.durations) if self.durations else self.max_duration
        return min(top, maximum) if maximum else top

    def price_of(self, resolution: str) -> float:
        if not self.price:
            return 0.0
        key = resolution.strip().lower()
        if key in self.price:
            return self.price[key]
        return self.price.get(self.resolutions[-1], max(self.price.values()))


def generic_model(model_id: str, settings: Mapping[str, str], *, base: VideoModel | None = None) -> VideoModel:
    """目录里没有的模型（新出的、聚合平台上的）：按 config.toml 里的填写来定能力。"""
    seed = base or VideoModel(model_id, frozenset({"text", "first_frame"}))

    def number(key: str, default: int) -> int:
        try:
            return int(str(settings.get(key) or default).strip())
        except ValueError:
            return default

    durations = tuple(
        int(item) for item in str(settings.get("durations") or "").replace("，", ",").split(",") if item.strip().isdigit()
    )
    modes = set(seed.modes)
    references = number("max_references", seed.max_references)
    if references > 0:
        modes.add("references")
    return VideoModel(
        model_id,
        frozenset(modes),
        min_duration=number("min_duration", seed.min_duration),
        max_duration=number("max_duration", seed.max_duration),
        durations=durations or seed.durations,
        resolutions=seed.resolutions,
        max_references=references,
        audio=seed.audio,
        price=seed.price,
        aspects=seed.aspects,
    )


def pick_model(catalog: Mapping[str, VideoModel], model_id: str, settings: Mapping[str, str]) -> VideoModel:
    """按模型 id 找能力表；带日期后缀的版本（如 wan2.7-i2v-2026-04-25）按前缀匹配。"""
    if model_id in catalog:
        found = catalog[model_id]
    else:
        prefix = next((key for key in sorted(catalog, key=len, reverse=True) if model_id.startswith(key)), None)
        found = catalog[prefix] if prefix else None
    if found is None:
        return generic_model(model_id, settings)
    if any(settings.get(key) for key in ("durations", "max_references")):
        return generic_model(model_id, settings, base=found)
    return found


def normalize_resolution(value: str, model: VideoModel, *, label: str) -> str:
    chosen = (value or model.resolutions[-1]).strip().lower()
    if chosen not in model.resolutions:
        # 分辨率决定价格，填错时直接报错，不悄悄换成别的档位。
        raise ValueError(f"{label} 的模型 {model.id} 只支持 {' / '.join(model.resolutions)}，不支持 {chosen}")
    return chosen


def config_price(settings: Mapping[str, str]) -> float | None:
    """config.toml 里手填的单价（元 / 秒）。"""
    raw = str(settings.get("price") or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def pricing_for(catalog: Mapping[str, VideoModel], default_model: str) -> Callable[[dict[str, str], str], float]:
    """生成「按配置和分辨率给出单价」的函数，挂在 ProviderSpec 上供费用估算用（不需要实例化服务）。"""

    def price(settings: dict[str, str], resolution: str) -> float:
        manual = config_price(settings)
        if manual is not None:
            return manual
        model = pick_model(catalog, str(settings.get("model") or default_model), settings)
        return model.price_of(resolution or str(settings.get("resolution") or model.resolutions[-1]))

    return price


def billing_for(catalog: Mapping[str, VideoModel], default_model: str) -> Callable[[dict[str, str], float], float]:
    """生成「想要多少秒 → 实际按多少秒计费」的函数（取到模型支持的档位）。"""

    def billed(settings: dict[str, str], seconds: float) -> float:
        model = pick_model(catalog, str(settings.get("model") or default_model), settings)
        return float(model.snap(seconds, maximum=_int(settings.get("max_duration"))))

    return billed


def resolutions_for(catalog: Mapping[str, VideoModel], default_model: str) -> Callable[[dict[str, str]], tuple[str, ...]]:
    """生成「按配置给出当前模型支持的分辨率」的函数，费用估算和生成用同一套草稿档规则。"""

    def resolutions(settings: dict[str, str]) -> tuple[str, ...]:
        return pick_model(catalog, str(settings.get("model") or default_model), settings).resolutions

    return resolutions


def draft_resolution(final: str, draft: str, supported: tuple[str, ...] | list[str] | None) -> str:
    """草稿档用的分辨率：模型没有 ``draft`` 这一档时用它支持的最低档；不比成片档低时不降。"""
    final, draft = final.strip().lower(), draft.strip().lower()
    if supported and draft not in supported:
        draft = min(supported, key=resolution_rank)
    return draft if 0 <= resolution_rank(draft) < resolution_rank(final) else final


def _int(value: object) -> int | None:
    try:
        return int(str(value)) if value not in (None, "") else None
    except ValueError:
        return None


# ---- 图片 -------------------------------------------------------------------

MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _shrink(path: Path, limit: int) -> Path:
    """图片超过服务商的大小上限时，缩成 2048 宽的 JPEG 再提交。"""
    if path.stat().st_size <= limit:
        return path
    from shotwright.media.ffmpeg import MediaError, extract_frame

    target = Path(tempfile.mkdtemp(prefix="shotwright-img-")) / "small.jpg"
    try:
        return extract_frame(path, target, at=0.0, width=2048)
    except MediaError as exc:
        raise ProviderError(f"图片太大且无法压缩：{exc}") from exc


def image_bytes(path: Path, *, limit: int = MAX_IMAGE_BYTES) -> tuple[bytes, str]:
    small = _shrink(path, limit)
    data = small.read_bytes()
    if small != path:
        import shutil

        shutil.rmtree(small.parent, ignore_errors=True)
    mime = sniff_image_type(data) or ""
    if not mime.startswith("image/"):
        raise ProviderError("图片必须是 PNG、JPEG、WebP 等常见格式")
    return data, mime


def data_url(path: Path, *, limit: int = MAX_IMAGE_BYTES) -> str:
    """``data:image/jpeg;base64,...``：万相、MiniMax、fal 等接受这种写法。"""
    data, mime = image_bytes(path, limit=limit)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def raw_base64(path: Path, *, limit: int = MAX_IMAGE_BYTES) -> str:
    """不带 ``data:`` 前缀的 base64：可灵要求这种写法。"""
    data, _mime = image_bytes(path, limit=limit)
    return base64.b64encode(data).decode("ascii")


def find_url(body: object, keys: tuple[str, ...] = ("video_url", "url", "download_url")) -> str:
    """在响应里找第一个视频下载地址（各家字段位置不同，宽松地找）。"""
    if isinstance(body, dict):
        for key in keys:
            value = body.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        for value in body.values():
            found = find_url(value, keys)
            if found:
                return found
    elif isinstance(body, list):
        for item in body:
            found = find_url(item, keys)
            if found:
                return found
    return ""


# ---- 异步任务型视频服务的公共骨架 ---------------------------------------------

_ACCOUNT_PROBLEMS = re.compile(
    r"HTTP (401|402|403|429)|余额|欠费|鉴权|未授权|限流|频率|balance|insufficient|quota|unauthori[sz]ed|"
    r"invalid ?api ?key|forbidden|rate.?limit|too many requests",
    re.IGNORECASE,
)


def is_account_problem(message: str) -> bool:
    """余额不足、Key 无效、限流这类拒绝和参考图无关，去掉参考图重试也没用。"""
    return bool(_ACCOUNT_PROBLEMS.search(message))



@dataclass
class Request:
    """一次生成请求（已经按模型能力整理好）。"""

    prompt: str
    mode: str
    seconds: int
    aspect: str
    first_frame: Path | None = None
    references: list[Path] = field(default_factory=list)


class TaskVideoProvider:
    """「提交任务 → 轮询 → 下载」型视频服务的公共部分。

    子类提供 ``LABEL``、``CATALOG``、``DEFAULT_MODEL``，实现 :meth:`_submit` 和 :meth:`_finish`。
    这里负责：选模型能力表、时长取档、分辨率校验、参考图张数、模型不支持参考图时改用首帧、
    提示词超长截断；参考图被拒绝（4xx，未扣费）时抛 ``ReferenceImagesUnsupported``，
    由调用方重拼提示词、改用首帧再提交。
    """

    LABEL = "视频服务"
    CATALOG: Mapping[str, VideoModel] = {}
    DEFAULT_MODEL = ""
    MAX_PROMPT = 5000
    CONSOLE = "服务商控制台"

    def __init__(self, settings: dict[str, str]):
        import logging

        from shotwright.providers import paid_task

        self.log = logging.getLogger(f"shotwright.providers.{type(self).__name__}")
        self.settings = settings
        self.model = str(settings.get("model") or self.DEFAULT_MODEL).strip()
        if not self.model:
            raise ValueError("没有填写模型（model）")
        self.profile = pick_model(self.CATALOG, self.model, settings)
        self.resolution = normalize_resolution(str(settings.get("resolution") or ""), self.profile, label=self.LABEL)
        self.min_duration = _int(settings.get("min_duration")) or self.profile.min_duration
        self.max_duration = self.profile.longest(_int(settings.get("max_duration")))
        self.generate_audio = str(settings.get("generate_audio") or "").strip().lower() in {"1", "true", "yes", "on"}
        self.watermark = str(settings.get("watermark") or "").strip().lower() in {"1", "true", "yes", "on"}
        self.proxy = settings.get("proxy") or None
        self.timeout = float(settings.get("timeout") or 60)
        self.poll_interval = float(settings.get("poll_interval") or 10)
        self.run_timeout = float(settings.get("run_timeout") or 1800)
        self._paid_task = paid_task

    # 供 services/visuals.py 读取的能力
    @property
    def resolutions(self) -> tuple[str, ...]:
        return self.profile.resolutions

    @property
    def max_reference_images(self) -> int:
        return self.profile.max_references if "references" in self.profile.modes else 0

    def _client(self):
        import httpx

        return httpx.Client(timeout=self.timeout, proxy=self.proxy, headers={"User-Agent": "Shotwright/0.4"})

    def _poll_settings(self, **kwargs):
        return self._paid_task.PollSettings(
            label=self.LABEL, interval=self.poll_interval, timeout=self.run_timeout, **kwargs
        )

    def _prompt(self, prompt: str) -> str:
        text = prompt.strip()
        if len(text) > self.MAX_PROMPT:
            self.log.warning("%s 提示词 %d 字，超过上限 %d，已截断", self.LABEL, len(text), self.MAX_PROMPT)
            text = text[: self.MAX_PROMPT]
        return text

    def plan(
        self, prompt: str, *, aspect: str, duration: float, first_frame: Path | None, references: list[Path] | None
    ) -> Request:
        """按模型能力决定这次用文生、首帧还是参考图。"""
        if not prompt.strip():
            raise ProviderError("缺少画面提示词")
        modes = self.profile.modes
        given = list(references or []) if first_frame is None else []
        refs = given[: self.max_reference_images]
        if given and not refs:
            # 模型不支持参考图：第一张参考图作为首帧，商品外观照样不走样。
            first_frame = given[0]
        mode = "references" if refs else "first_frame" if first_frame is not None else "text"
        if mode == "first_frame" and "first_frame" not in modes:
            raise ProviderError(f"{self.LABEL} 的模型 {self.model} 不支持用图片作为首帧，请换一个图生视频模型")
        if mode == "text" and "text" not in modes:
            raise ProviderError(f"{self.LABEL} 的模型 {self.model} 不能只凭文字生成，需要先选首帧图片")
        seconds = self.profile.snap(duration, minimum=self.min_duration, maximum=self.max_duration)
        return Request(self._prompt(prompt), mode, seconds, aspect, first_frame, refs)

    def generate(
        self,
        prompt: str,
        *,
        aspect: str,
        duration: float,
        output: Path,
        first_frame: Path | None = None,
        reference_video: Path | None = None,
        reference_images: list[Path] | None = None,
        on_task: Callable[[str], None] | None = None,
    ) -> Path:
        from shotwright.providers.base import ReferenceImagesUnsupported, UnconfirmedTaskError

        if reference_video is not None:
            raise ProviderError(f"{self.LABEL} 暂不支持改画面")
        request = self.plan(
            prompt, aspect=aspect, duration=duration, first_frame=first_frame, references=reference_images
        )
        with self._client() as client:
            try:
                task_id = self._submit(client, request)
            except UnconfirmedTaskError:
                raise
            except ProviderError as exc:
                if request.mode != "references" or is_account_problem(str(exc)):
                    raise
                # 远端明确拒绝（4xx，没扣费）：交给调用方去掉提示词里按编号写的参考图要求、
                # 改用首帧重新提交（提示词由调用方拼，这里不自己重试）。
                raise ReferenceImagesUnsupported(str(exc)) from exc
            if on_task is not None:
                on_task(task_id)
            return self._finish(client, task_id, output)

    def resume(self, task_id: str, *, output: Path) -> Path:
        """继续轮询一个已提交的任务（进程重启后恢复用）。"""
        with self._client() as client:
            return self._finish(client, task_id, output)

    def _submit(self, client, request: Request) -> str:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def _finish(self, client, task_id: str, output: Path) -> Path:  # pragma: no cover - 子类实现
        raise NotImplementedError