"""服务端错误提示的英文版。

界面语言由浏览器的 ``Accept-Language`` 决定：请求里带 ``en`` 时，返回的 ``detail``
换成英文。只翻译**固定的**提示文案；带变量的消息（例如「镜头 2 配音失败：…」）
保持原文，因为拆模板比逐条翻译更容易出错，而这类消息通常会附带英文的底层原因。

新增提示时在 :data:`EN` 里补一条中文→英文的对照即可；没有对照时按原文返回。
"""

from __future__ import annotations

from starlette.requests import Request

# 中文提示 → 英文。键必须和代码里抛出的字符串完全一致才能命中。
EN: dict[str, str] = {
    # 通用
    "项目不存在": "Project not found",
    "镜头或素材不存在": "Shot or asset not found",
    "镜头不存在": "Shot not found",
    "素材不存在": "Asset not found",
    "任务不存在": "Job not found",
    "任务已结束或不存在": "Job has already finished or does not exist",
    "文件不存在": "File not found",
    "无效的 id": "Invalid id",
    "路径越界": "Path escapes the project directory",
    "项目路径越界": "Project path escapes the storage directory",
    # 字体
    "找不到这个字体": "Font not found",
    "只支持 .ttf 和 .otf 字体文件": "Only .ttf and .otf font files are supported",
    "字体文件超过 40MB": "Font file is larger than 40MB",
    "无法读取这个字体文件": "This font file could not be read",
    "和内置字体重名，请先改个文件名": "A built-in font has the same file name; please rename the file first",
    "内置字体不能删除": "Built-in fonts cannot be deleted",
    # 校验
    "不支持的语言": "Unsupported language",
    "请填写视频诉求": "Please describe what the video should say",
    "请填写视频文案": "Please provide a script",
    "所选素材不存在": "The selected asset does not exist",
    "新顺序必须包含全部镜头且不能重复": "The new order must list every shot exactly once",
    "背景音乐素材不存在": "The background music asset does not exist",
    "镜头 id 不能重复": "Shot ids must be unique",
    "首帧必须是图片": "The first frame must be an image",
    "出点必须晚于入点": "The out point must be after the in point",
    "只有视频镜头可以拆分": "Only video shots can be split",
    "无法读取素材时长，不能拆分": "Cannot read the clip length, so the shot cannot be split",
    "拆分点太靠近片段两端": "The split point is too close to the start or end of the clip",
    "后面没有可以合并的镜头": "There is no following shot to merge with",
    "请先选中素材再设置入出点或取景": "Select a clip before setting in/out points or framing",
    "颜色必须是 #RRGGBB 格式": "Colours must use the #RRGGBB format",
    "字体只能是 resources/fonts 下的文件名": "Fonts must be a file name inside resources/fonts",
    "素材文件名不能包含路径": "Asset file names must not contain a path",
    "id 只能包含字母、数字、下划线和连字符，长度 1–64": "Ids may only contain letters, digits, underscores and hyphens, 1–64 characters",
    # 冲突
    "镜头已锁定，请先解锁": "This shot is locked. Unlock it first.",
    "这个镜头正在生成，请等它完成": "This shot is already being generated. Please wait for it to finish.",
    "这个项目正在渲染，请等它结束，或加 ?force=true 强制删除": "This project is rendering. Wait for it to finish, or pass ?force=true to delete it anyway.",
    # 上传
    "文件超过 500MB": "The file is larger than 500 MB",
    "无法识别上传的文件，请上传常见的视频、图片或音频格式": "Could not read the uploaded file. Please upload a common video, image or audio format.",
    "上传的文件既没有画面也没有声音": "The uploaded file has neither video nor audio",
    # 素材与生成
    "这个镜头没有检索词，请先填写英文检索词": "This shot has no search terms. Please add a few English terms.",
    "这个镜头没有画面提示词": "This shot has no visual prompt",
    "当前的 AI 视频服务不支持改画面": "The current AI video service cannot edit an existing video",
    "改画面需要先选中一段视频": "Select a video for this shot before editing it",
    "缺少画面提示词": "Missing visual prompt",
    "这个镜头的画面来源不需要检索或生成": "This shot's visual source does not need searching or generation",
    "没有找到合适的素材，试试换几个检索词": "No suitable footage found. Try different search terms.",
    "没有找到合适的背景音乐，试试换个检索词": "No suitable music found. Try different search terms.",
    "检索到的文件没有声音": "The retrieved file has no audio",
    "素材不包含画面": "The asset has no video stream",
    "素材下载到空文件": "The downloaded asset was empty",
    "素材文件过大": "The asset file is too large",
    "背景音乐下载到空文件": "The downloaded music file was empty",
    "背景音乐文件过大": "The music file is too large",
    "首帧图片超过 30MB": "The first-frame image is larger than 30 MB",
    "首帧必须是 PNG、JPEG、WebP 等常见图片格式": "The first frame must be a common image format such as PNG, JPEG or WebP",
    # 配音
    "未安装 edge-tts": "edge-tts is not installed",
    "Edge 配音返回的音频无法解析": "Edge TTS returned audio that could not be read",
    "Edge 配音返回了空音频": "Edge TTS returned empty audio",
    "大模型没有返回 JSON": "The model did not return JSON",
    "大模型返回的 JSON 无法解析": "The model returned JSON that could not be parsed",
    "大模型返回的格式无法解析": "The model returned an unexpected format",
    "大模型没有返回任何镜头": "The model returned no shots",
    # MiniMax 配音（配置与返回格式）
    "MiniMax 没有返回可下载的音频地址": "MiniMax did not return a downloadable audio URL",
    "MiniMax 返回的格式无法解析": "MiniMax returned an unexpected format",
    "MiniMax 返回的音频数据损坏": "MiniMax returned corrupted audio data",
    "MiniMax 返回的音频大小异常": "MiniMax returned audio of an unexpected size",
    "MiniMax 返回的音频过大": "The audio returned by MiniMax is too large",
    "region 不能为空": "region must not be empty",
    "min_duration 必须大于 0 且不大于 max_duration": "min_duration must be greater than 0 and no greater than max_duration",
    # 素材库
    "Pexels API Key 无效": "The Pexels API key is invalid",
    "Pixabay 拒绝了请求，请检查 API Key": "Pixabay rejected the request. Check the API key.",
    "Pixabay 请求过于频繁，请稍后再试": "Pixabay rate limit reached. Try again later.",
    "Coverr 没有返回视频文件": "Coverr did not return a video file",
    "Coverr 返回的格式无法解析": "Coverr returned an unexpected format",
    "Pexels 返回的格式无法解析": "Pexels returned an unexpected format",
    "Pixabay 返回的格式无法解析": "Pixabay returned an unexpected format",
    "Internet Archive 返回的格式无法解析": "Internet Archive returned an unexpected format",
    # 渲染
    "项目里还没有镜头": "This project has no shots yet",
    # 语言版本
    "目标语言和当前项目相同": "The target language is the same as this project's",
}


def wants_english(request: Request | None) -> bool:
    """按 ``Accept-Language`` 判断要不要用英文。中文优先于英文。"""
    if request is None:
        return False
    header = request.headers.get("accept-language", "")
    if not header:
        return False
    # 只看到第一个语言标签就够了：浏览器送来的顺序就是优先级。
    first = header.split(",")[0].strip().lower()
    if first.startswith("zh"):
        return False
    return first.startswith("en")


def translate(detail: str, request: Request | None) -> str:
    """把固定提示翻成英文；没有对照或不需要英文时原样返回。"""
    if not wants_english(request):
        return detail
    return EN.get(detail, detail)
