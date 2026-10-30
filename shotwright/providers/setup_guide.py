"""服务设置页上每个服务的「怎么开通」说明：去哪里注册、拿哪个 Key、要开通什么、怎么收费。

放在一处统一维护，不散落在各个 Provider 里；没有写说明的服务界面上只显示文档链接。
价格只写计费方式，不写具体数字：各家调价频繁，具体参考价见渲染面板的费用预估。
"""

from __future__ import annotations

ARK = "火山方舟控制台（console.volcengine.com/ark）"
ARK_EN = "the Volcengine Ark console (console.volcengine.com/ark)"

GUIDES: dict[tuple[str, str], tuple[str, str]] = {
    ("llm", "doubao"): (
        f"注册火山引擎账号，进入{ARK}：在「API Key 管理」里创建一个 Key，再在「开通管理」里开通豆包 Seed 模型。"
        "同一个 Key 也用于 Seedance（AI 视频）和 Seedream（AI 图片），开通对应模型即可，不用重复填写。按用量计费。",
        f"Sign up for Volcengine and open {ARK_EN}: create a key under API Key Management, then enable the Doubao "
        "Seed model under Model Activation. The same key also works for Seedance (AI video) and Seedream (AI images) "
        "once those models are enabled. Billed by usage.",
    ),
    ("llm", "deepseek"): (
        "在 platform.deepseek.com 注册，进入「API keys」创建 Key，充值后即可使用。按用量计费。",
        "Sign up at platform.deepseek.com, create a key under API keys and top up your balance. Billed by usage.",
    ),
    ("llm", "kimi"): (
        "在 platform.moonshot.cn 注册，进入「API Key 管理」创建 Key。按用量计费。",
        "Sign up at platform.moonshot.cn and create a key under API Key Management. Billed by usage.",
    ),
    ("llm", "qwen"): (
        "在阿里云百炼控制台开通服务，进入「API-KEY」创建 Key。同一个 Key 也用于通义万相（AI 视频）。按用量计费。",
        "Activate Alibaba Cloud Model Studio and create a key under API-KEY. The same key also works for Wan "
        "(AI video). Billed by usage.",
    ),
    ("llm", "openai"): (
        "填写任何兼容 OpenAI 接口的服务：Base URL 写到版本号（例如 /v1），再填模型名和 Key。",
        "Any OpenAI-compatible service works: set the Base URL up to the version (e.g. /v1), the model name and key.",
    ),
    ("stock", "pixabay"): (
        "免费：在 pixabay.com 注册后打开 pixabay.com/api/docs，页面里的「Your API key」就是 Key。"
        "素材可商用，免署名。",
        "Free: sign up at pixabay.com, then open pixabay.com/api/docs; the key is shown under “Your API key”. "
        "Footage is free for commercial use without attribution.",
    ),
    ("stock", "pexels"): (
        "免费：在 pexels.com/api 申请 Key（目前暂停发放新 Key，已有 Key 可以继续用）。",
        "Free: request a key at pexels.com/api (new keys are currently paused; existing keys keep working).",
    ),
    ("stock", "coverr"): (
        "免费：在 coverr.co/developers 申请 API Key。",
        "Free: request an API key at coverr.co/developers.",
    ),
    ("video_gen", "seedance"): (
        f"用火山方舟的 Key：留空时自动使用「豆包 Seed」里填的那一个。需要在{ARK}的「开通管理」里开通 Seedance 模型。"
        "按生成的视频秒数收费。",
        f"Uses your Volcengine Ark key: left empty, it borrows the one entered for Doubao Seed. Enable the Seedance "
        f"model in {ARK_EN}. Billed per second of generated video.",
    ),
    ("image_gen", "seedream"): (
        f"用火山方舟的 Key：留空时自动使用「豆包 Seed」里填的那一个。需要在{ARK}里开通 Seedream 模型。按生成的张数收费。",
        f"Uses your Volcengine Ark key: left empty, it borrows the one entered for Doubao Seed. Enable the Seedream "
        f"model in {ARK_EN}. Billed per image.",
    ),
    ("image_gen", "openai"): (
        "填写任何兼容 OpenAI /images/generations 的服务：Base URL、模型名和 Key。按张收费。",
        "Any OpenAI-compatible /images/generations service: set the Base URL, model and key. Billed per image.",
    ),
    ("video_gen", "wan"): (
        "用阿里云百炼的 Key：留空时自动使用「通义千问」里填的那一个。按生成的视频秒数收费。",
        "Uses your Alibaba Cloud Model Studio key: left empty, it borrows the one entered for Qwen. "
        "Billed per second of generated video.",
    ),
    ("video_gen", "kling"): (
        "在可灵 AI 开放平台创建应用，拿到 Access Key 和 Secret Key（或直接填 API Key）。按生成次数和时长收费。",
        "Create an app on the Kling AI developer platform and copy its Access Key and Secret Key (or an API key). "
        "Billed per generation and duration.",
    ),
    ("video_gen", "minimax"): (
        "在 MiniMax 开放平台创建 API Key；留空时自动使用「MiniMax 配音」里填的那一个。按生成次数收费。",
        "Create an API key on the MiniMax platform; left empty, it borrows the one entered for MiniMax voice. "
        "Billed per generation.",
    ),
    ("video_gen", "fal"): (
        "在 fal.ai 注册并创建 Key，可以用 Veo、Vidu、Luma 等多家模型。按各模型的价格收费。",
        "Sign up at fal.ai and create a key to use Veo, Vidu, Luma and others. Billed at each model's price.",
    ),
    ("tts", "edge"): (
        "免费，不用填任何东西：直接使用微软 Edge 的在线语音，需要能联网。",
        "Free, nothing to fill in: uses Microsoft Edge's online voices; needs an internet connection.",
    ),
    ("tts", "silent"): (
        "不出声，只用来离线预览画面和字幕。",
        "No sound; only for previewing pictures and subtitles offline.",
    ),
    ("tts", "azure"): (
        "在 Azure 门户创建「语音服务」资源，复制它的 Key 和区域（例如 eastasia）。有免费额度，超出按字符收费。",
        "Create a Speech resource in the Azure portal and copy its key and region (e.g. eastasia). "
        "Includes a free tier; beyond that, billed per character.",
    ),
    ("tts", "siliconflow"): (
        "在 cloud.siliconflow.cn 注册并创建 API Key。按字符收费。",
        "Sign up at cloud.siliconflow.cn and create an API key. Billed per character.",
    ),
    ("tts", "minimax"): (
        "在 MiniMax 开放平台创建 API Key。同一个 Key 也用于海螺 AI 视频。按字符收费。",
        "Create an API key on the MiniMax platform. The same key also works for Hailuo AI video. "
        "Billed per character.",
    ),
    ("tts", "elevenlabs"): (
        "在 elevenlabs.io 注册，在个人设置里复制 API Key。有免费额度，超出按字符收费。",
        "Sign up at elevenlabs.io and copy the API key from your profile. Includes a free tier; then per character.",
    ),
    ("music", "archive"): (
        "免费，不用填：从 Internet Archive 检索可商用的背景音乐，只保留授权允许的曲目。",
        "Free, nothing to fill in: searches Internet Archive for background music and keeps only permitted licenses.",
    ),
    ("asr", "faster_whisper"): (
        '免费、本地运行，原片不出本机。需要安装语音识别组件（pip install -e ".[asr]"）；'
        "第一次识别时会下载模型（small 约 480MB）。",
        "Free and local; your footage never leaves this computer. Install the speech extra "
        '(pip install -e ".[asr]"); the model downloads on first use (small is about 480MB).',
    ),
    ("asr", "openai"): (
        "任何兼容 OpenAI /audio/transcriptions、能返回逐词时间的接口（OpenAI whisper-1、Groq 等）。按时长收费。",
        "Any OpenAI-compatible /audio/transcriptions endpoint with word timestamps (OpenAI whisper-1, Groq…). "
        "Billed by duration.",
    ),
}


def guide(kind: str, provider_id: str) -> tuple[str, str]:
    """（中文说明，英文说明）；没有写说明时两个都是空字符串。"""
    return GUIDES.get((kind, provider_id), ("", ""))
