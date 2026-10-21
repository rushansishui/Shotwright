# Shotwright 架构说明

Shotwright 以**镜头（Shot）**为基本单位：一个视频是一个项目（Project），项目由有序的镜头组成，每个镜头引用素材（Asset）。渲染按镜头增量进行，改一个镜头只重做这个镜头和最后的拼接导出。

## 分层

```text
┌──────────────────────────────────────────────────────────────┐
│ 入口层     web/（React）   shotwright/api（FastAPI）   shotwright/cli.py │
├──────────────────────────────────────────────────────────────┤
│ 服务层     services/storyboard.py   services/visuals.py   api/jobs.py   │
├──────────────────────────────────────────────────────────────┤
│ 渲染引擎   pipeline/render.py（阶段 + 指纹缓存）  media/（FFmpeg、字幕）  │
├──────────────────────────────────────────────────────────────┤
│ Provider   providers/{llm,tts,stock,video_gen,image_gen}/ + registry.py │
├──────────────────────────────────────────────────────────────┤
│ 核心       core/models.py  core/store.py  core/fingerprint.py  core/languages.py │
└──────────────────────────────────────────────────────────────┘
```

依赖只能自上而下：入口层不直接调用 Provider，Provider 不依赖服务层，核心层不依赖任何上层模块。

## 目录

```text
shotwright/
├── core/
│   ├── models.py        Project / Shot / ShotVisual / Asset 等 Pydantic 模型
│   ├── store.py         项目存储：原子写入、素材按 SHA-256 去重、路径越界校验
│   ├── fingerprint.py   阶段指纹
│   └── ids.py
├── providers/
│   ├── base.py          ProviderSpec、ConfigField、各类别的协议（Protocol）
│   ├── registry.py      注册表：显式列出内置模块、惰性导入、按配置实例化
│   ├── llm/openai_compat.py  OpenAI 兼容实现 + 豆包 Seed / DeepSeek / Kimi / 通义千问预设
│   ├── tts/edge.py      Edge 在线语音（免费）
│   ├── tts/silent.py    静音配音（离线预览、测试）
│   ├── stock/pixabay.py      默认素材库，方向与画布一致的素材优先
│   ├── stock/pexels.py
│   ├── paid_task.py     收费异步任务：提交不重试、轮询、下载
│   ├── video_gen/seedance.py      火山方舟 Seedance：文生视频、首帧图生视频
│   └── image_gen/openai_images.py Seedream / OpenAI 兼容文生图
├── services/
│   ├── storyboard.py    主题 → 文案 → 镜头（大模型或按句切分）
│   ├── visuals.py       检索 / 生成候选素材，登记上传文件
│   └── durations.py     时长估算
├── media/
│   ├── ffmpeg.py        FFmpeg / FFprobe 封装（禁止网络协议）
│   └── subtitles.py     字幕切分、像素级换行、ASS / SRT
├── pipeline/render.py   增量渲染
├── api/                 FastAPI 应用、请求体、任务队列
├── config.py            config.toml + 环境变量
└── cli.py
web/                     React + Vite 前端
resources/fonts/         内置字体（Noto Sans SC，OFL）
tests/                   pytest
```

## 数据模型

| 对象 | 说明 |
| --- | --- |
| `Project` | 一个视频的全部状态：画布（比例、帧率）、默认配音、字幕样式、背景音乐、镜头列表、素材表、修订号 |
| `Shot` | 文案、固定时长（为空则跟随配音）、画面 `ShotVisual`、可选的单独配音设置 |
| `ShotVisual` | 画面来源 `mode`（`stock` 检索 / `ai` AI 视频 / `image` AI 图片 / `upload` 上传 / `color` 纯色）、检索词、提示词、候选素材、已选素材、锁定标记 |
| `Asset` | 落盘的媒体文件：类型、来源、SHA-256、尺寸与时长、作者和授权信息 |

存储结构：

```text
storage/projects/<项目id>/
├── project.json      项目状态（原子替换写入）
├── assets/           素材按来源分目录，文件名 <sha256 前 16 位>.<扩展名>
│   ├── ai-video/     AI 生成的视频（Seedance 等）
│   ├── ai-image/     AI 生成的图片（Seedream 等）
│   ├── stock/        素材库下载（Pixabay、Pexels）
│   ├── upload/       用户上传的画面和音频
│   └── other/        其他来源
├── cache/            voice/ shots/ assembly/ final/，按指纹命名，可随时删除
└── renders/<渲染id>/ video.mp4、subtitles.srt、render.json
```

素材的子目录记在 `Asset.folder`。v0.3 之前的项目素材直接放在 `assets/` 下，服务或命令行启动时会自动按来源移到对应子目录（先移动文件再保存项目，中途中断也能找到文件）；渲染缓存按内容哈希计算，移动后不会重新渲染。

所有 id 只允许 `[A-Za-z0-9_-]`，路径都在项目目录内解析并校验，杜绝路径穿越。密钥不进入项目文件。

## 渲染流水线

```text
每个镜头：  画面准备 ─→ 配音 ─→ 镜头渲染（画面缩放裁切、循环到时长、混入配音）
                                      │
整片：                        拼接 ─→ 字幕与背景音乐 ─→ 导出
```

| 阶段 | 范围 | 指纹输入（任一变化即重做） |
| --- | --- | --- |
| 配音 | 每个镜头 | 文案、配音服务、声音、语速、音量 |
| 镜头渲染 | 每个镜头 | 配音指纹、时长、所选素材的 SHA-256（或纯色）、适配方式、图片运动效果版本、画布 |
| 拼接 | 整片 | 所有镜头渲染指纹及顺序 |
| 字幕与背景音乐 | 整片 | 拼接指纹、字幕条目与样式、字体文件、背景音乐及音量 |

规则：

1. **画面准备**：渲染时，没有画面且未锁定的镜头会自动检索或生成一次。`ai` / `image` 镜头只有在提交渲染时确认了收费（`confirm_charge`）才会调用 AI；未确认、未配置或生成失败时，有检索词就改用素材检索，仍拿不到素材才使用纯色画面。每种情况都在结果里给出提示，不中断渲染。
2. **图片镜头**：图片素材（上传或 AI 图片）渲染时加慢推镜头（Ken Burns）：先按两倍画布适配，再用 `zoompan` 从 1.0 匀速放大到 1.1 倍。
3. **锁定**：锁定的镜头不会被自动补素材，也不允许重新检索。
4. **配音失败会中断渲染**，并指出是第几个镜头。
5. 渲染期间对项目的编辑不会被覆盖：自动补的素材只合并进最新的项目文件。
6. 字幕在每个镜头的配音时段内，按标点切分、按字数比例分配时间，按真实字体的像素宽度换行，并尽量让两行等长。

## Provider 插件

每类外部服务都是"规格声明 + 工厂函数 + 协议"：

```python
registry.register(
    ProviderSpec(kind="stock", id="pexels", name="Pexels",
                 fields=(ConfigField("api_key", "API Key", required=True, secret=True),)),
    PexelsStock,   # 接收配置字典，返回实现了 StockProvider 协议的对象
)
```

- 配置来自 `config.toml` 的 `[providers.<类别>.<id>]`，可用环境变量 `SHOTWRIGHT_<类别>_<ID>_<字段>` 覆盖。
- `/api/v2/providers` 和网页的"服务状态"页直接遍历注册表生成，只报告是否已配置，不返回任何配置值。
- 收费的服务设置 `paid=True`，界面会标出"收费"。
- `shared_key_from` 让同一平台的服务共用一把 Key：Seedance、Seedream 自己的 `api_key` 为空时使用 `[providers.llm.doubao]` 的方舟 Key。

### 收费生成

AI 视频和 AI 图片按次计费，处理规则沿用 MoneyPrinterTurbo 的做法：

| 场景 | 处理 |
| --- | --- |
| 用户没有确认 | 抛 `ChargeConfirmationRequired`，API 返回 402，不发出任何请求 |
| 提交时超时、5xx、重定向、响应无法解析 | 远端可能已经建好任务并扣费：抛 `UnconfirmedTaskError`，**不自动重试**，提示到控制台核对 |
| 提交返回 4xx | 远端明确拒绝、没有扣费，直接报错 |
| 轮询状态时网络抖动、429、5xx | 查询不收费，线性退避重试；超过次数或总时长后报"无法确认"并附任务 id |
| 任务 `failed` / `cancelled` / `expired` | 报错并附服务商给出的原因 |
| 成功 | 立即下载结果（Seedance 的地址约 24 小时失效），按 SHA-256 入库并选中 |

AI 视频镜头已选中一张图片时（例如先用 AI 图片生成、或上传了一张图），会把这张图作为首帧走图生视频。

新增一家服务的步骤见 [PROVIDERS.md](PROVIDERS.md)。

### 大模型预设

`llm` 类别内置四个预设，共用 OpenAI 兼容实现，只是默认的 Base URL、推荐模型和超时不同。在 `[defaults]` 里选好后，只需在对应小节填写 `api_key`；`model` 可以覆盖。

| 预设 | Base URL | 推荐模型 |
| --- | --- | --- |
| `doubao` 豆包 Seed（默认） | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-2-1-pro-260915` |
| `deepseek` | `https://api.deepseek.com` | `deepseek-flash` |
| `kimi` | `https://api.moonshot.cn/v1` | `kimi-k3` |
| `qwen` 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| `openai`（自定义） | 自行填写 | 自行填写 |

Base URL 误填成完整的 `/chat/completions` 地址时会自动纠正。

## 多语种

- **视频语言**：`Project.language` 使用 BCP-47 代码（`zh-CN`、`en-US`、`ja-JP`……），语言目录在 `core/languages.py`：每种语言给大模型的语言名、可选的 Edge 声音、默认字幕字体。切换语言时自动换成该语言的默认声音和字体，文案不变。
- **字幕**：按可断行的最小单位换行——中日文、泰文逐字，拉丁、西里尔、韩文按词；泰文、越南语的组合符号不会被拆到行首。字体缺字时（例如中文字体显示韩文）渲染会自动换用 `resources/fonts` 里能显示的字体并给出提示。
- **语言版本**：`POST /projects/{id}/versions` 让大模型翻译标题和每个镜头的文案，生成一个新项目。新项目与原项目共用 `group`，镜头 id、画面素材（硬链接，不额外占磁盘）、锁定状态都相同，配音和字幕换成目标语言。各语言版本独立渲染、独立缓存。
- **界面**：`web/src/messages.ts` 存中英文界面文字，`web/src/i18n.tsx` 提供切换，选择保存在浏览器里。服务端返回的错误提示目前只有中文。

## 日志

`storage/logs/shotwright.log`（存储目录的上一级），每天零点切分，保留 14 天。记录：收费任务的模型、任务 id、结果和用时；检索 / 生成的开始与结果；渲染任务的开始、完成、提示和失败堆栈；接口报错。所有日志经过脱敏，配置和环境变量里的 API Key 会被替换成 `***`。

## API

所有接口在 `/api/v2` 下，完整文档见运行时的 `/docs`。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/providers`、`/voices?language=` | 服务状态、某种语言可选的声音 |
| GET | `/languages` | 支持的视频语言 |
| GET / POST | `/projects/{id}/versions` | 同一视频的语言版本；POST `{"language": "en-US"}` 翻译出新版本 |
| GET / POST | `/projects` | 列出、创建项目（`mode`：`llm` / `split` / `empty`） |
| GET / PATCH / DELETE | `/projects/{id}` | 读取、修改设置、删除 |
| POST | `/projects/{id}/shots` | 新增镜头（可指定插在哪个镜头之后） |
| PUT | `/projects/{id}/shots/order` | 调整镜头顺序 |
| PATCH / DELETE | `/projects/{id}/shots/{shot}` | 修改、删除镜头 |
| POST | `/projects/{id}/shots/{shot}/candidates` | 为镜头检索或生成候选素材；收费生成需带 `{"confirm_charge": true}`，否则返回 402 |
| POST | `/projects/{id}/assets` | 上传图片、视频或音频 |
| GET | `/projects/{id}/assets/{asset}` | 读取素材文件 |
| POST | `/projects/{id}/renders` | 提交渲染任务；`{"confirm_charge": true}` 时允许为缺画面的 AI 镜头收费生成 |
| GET | `/renders/{job}`、`/projects/{id}/renders` | 任务进度、历史渲染 |
| GET | `/projects/{id}/renders/{render}/video.mp4` | 下载成片（还有 `subtitles.srt`） |

渲染任务在进程内的单线程队列中执行。需要多进程或多机部署时，把 `api/jobs.py` 换成 Redis 等外部队列即可，接口不变。

## 安全约束

- FFmpeg 以参数列表调用，不经过 shell；输入限制为本地文件协议。
- 上传文件限制 500MB，先用 FFprobe 识别类型再入库；素材下载限制 500MB。
- 错误信息不包含密钥；`/providers` 不返回配置值。
- 字体只能是 `resources/fonts` 中的文件名。
