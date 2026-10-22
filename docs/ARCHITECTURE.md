# Shotwright 架构说明

Shotwright 以**镜头（Shot）**为基本单位：一个视频是一个项目（Project），项目由有序的镜头组成，每个镜头引用素材（Asset）。渲染按镜头增量进行，改一个镜头只重做这个镜头和最后的拼接导出。

## 分层

```text
┌──────────────────────────────────────────────────────────────┐
│ 入口层     web/（React）   shotwright/api（FastAPI）   shotwright/cli.py │
├──────────────────────────────────────────────────────────────┤
│ 服务层     services/（分镜、画面、剪辑、原片、费用、本地化）  api/jobs.py │
├──────────────────────────────────────────────────────────────┤
│ 渲染引擎   pipeline/（整片调度、镜头、配音、元数据）  media/（FFmpeg、字幕） │
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
│   ├── stock/coverr.py       免 Key，横屏为主；下载地址要二次查询
│   ├── tts/edge.py      Edge 在线语音（免费，返回逐词时间戳）
│   ├── tts/silent.py    静音配音（离线预览、测试）
│   ├── tts/http_tts.py  收费配音的公共部分：发请求、校验音频、抹掉密钥
│   ├── tts/azure.py     Azure 官方语音（SSML）
│   ├── tts/siliconflow.py   硅基流动
│   ├── tts/minimax.py   MiniMax（返回 JSON，需二次下载或十六进制解码）
│   ├── tts/elevenlabs.py    ElevenLabs
│   ├── music/archive.py Internet Archive 背景音乐（免 Key，按授权过滤）
│   ├── paid_task.py     收费异步任务：提交不重试、轮询、下载
│   ├── video_gen/seedance.py      火山方舟 Seedance：文生视频、首帧图生视频、视频转视频（改画面）
│   └── image_gen/openai_images.py Seedream / OpenAI 兼容文生图
├── services/
│   ├── storyboard.py    主题 → 文案 → 镜头（大模型或按句切分）
│   ├── visuals.py       检索（带检索缓存）/ 生成候选素材，登记上传文件，改画面，恢复收费任务
│   ├── editing.py       镜头内剪辑：拆分、合并镜头（改入出点，不切文件）
│   ├── footage.py       原片导入：按画面变化切镜，每个镜头引用原片的一段
│   ├── cost.py          费用预估与累计（参考单价）
│   ├── music.py         背景音乐：提炼检索词、检索、登记候选
│   ├── localize.py      语言版本：翻译文案和叠加文字、共用素材
│   └── durations.py     时长估算
├── media/
│   ├── ffmpeg.py        FFmpeg / FFprobe 封装（禁止网络协议）
│   └── subtitles.py     字幕切分、像素级换行、ASS / SRT
├── pipeline/
│   ├── render.py        整片调度：画面准备、时间轴（含转场重叠）、拼接、字幕与叠加、导出
│   ├── voice.py         配音阶段
│   ├── shots.py         镜头渲染：入出点、慢放定格 / 循环、取景、原声与压低
│   ├── metadata.py      成片元数据：AI 生成标识、背景音乐署名
│   └── render_types.py  阶段记录、渲染结果等公共类型
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
| `Project` | 一个视频的全部状态：画布（比例、帧率）、默认配音、字幕样式、背景音乐、叠加文字与图形、转场、AI 角标、主体库（参考素材）、镜头列表、素材表、累计生成费用、修订号 |
| `Shot` | 文案、固定时长（为空则跟随配音）、画面 `ShotVisual`、可选的单独配音设置 |
| `ShotVisual` | 画面来源 `mode`（`stock` 检索 / `ai` AI 视频 / `image` AI 图片 / `upload` 上传 / `color` 纯色）、检索词、提示词、候选素材、已选素材、首帧、锁定标记、未取回的收费任务 `pending`，以及剪辑设置（见下） |
| `Asset` | 落盘的媒体文件：类型、来源、SHA-256、尺寸与时长、作者和授权信息 |

存储结构：

```text
storage/projects/<项目id>/
├── project.json      项目状态（原子替换写入）
├── assets/           素材按来源分目录，文件名 <sha256 前 16 位>.<扩展名>
│   ├── ai-video/     AI 生成的视频（Seedance 等）
│   ├── ai-image/     AI 生成的图片（Seedream 等）
│   ├── stock/        素材库下载（Pixabay、Pexels、Coverr）
│   ├── upload/       用户上传的画面和音频
│   ├── music/        自动检索到的背景音乐
│   └── other/        其他来源
├── cache/            voice/ shots/ assembly/ final/，按指纹命名，可随时删除
│                     stock-search/ 检索结果缓存，相同检索词 7 天内复用
└── renders/<渲染id>/ video.mp4、subtitles.srt、render.json
```

镜头内剪辑的设置都在 `ShotVisual` 里：

| 字段 | 说明 |
| --- | --- |
| `trim_start` / `trim_end` | 视频素材的入点、出点（秒）；出点为空表示到素材结尾 |
| `fill` | 选段比镜头短时的补足方式：`slow_hold`（默认，先慢放到不低于 0.8 倍，再定格最后一帧）、`hold`（直接定格）、`loop`（循环） |
| `fit`、`focus_x` / `focus_y` | 适配方式：`cover` 铺满裁切（按取景中心裁）、`contain` 完整加黑边、`blur` 完整加模糊背景 |
| `source_volume` | 原声音量，0 表示不用原声；有配音时用侧链压缩自动压低 |
| `edit_asset` | 入出点和取景是针对哪个素材设的；换了素材后这些设置自动失效，不会把旧素材的入出点套到新素材上 |

原片导入后不再切成单独的文件：每个镜头都引用同一段原片，用入出点选出自己那一段，原声默认保留。拆分、合并镜头只是改入出点，可以随时撤回重来。

素材的子目录记在 `Asset.folder`。v0.3 之前的项目素材直接放在 `assets/` 下，服务或命令行启动时会自动按来源移到对应子目录（先移动文件再保存项目，中途中断也能找到文件）；渲染缓存按内容哈希计算，移动后不会重新渲染。

所有 id 只允许 `[A-Za-z0-9_-]`，路径都在项目目录内解析并校验，杜绝路径穿越。密钥不进入项目文件。

## 渲染流水线

```text
每个镜头：  画面准备 ─→ 配音 ─→ 镜头渲染（选段、慢放定格、取景、混入原声与配音）
                                      │
整片：                        拼接 ─→ 字幕与背景音乐 ─→ 导出
```

| 阶段 | 范围 | 指纹输入（任一变化即重做） |
| --- | --- | --- |
| 配音 | 每个镜头 | 文案、配音服务、声音、语速、音量 |
| 镜头渲染 | 每个镜头 | 配音指纹、时长、所选素材的 SHA-256（或纯色）、适配方式与取景、选段与补足方式、原声音量、图片运动效果版本、画布、草稿 / 成片 |
| 拼接 | 整片 | 所有镜头渲染指纹及顺序、转场类型与时长 |
| 字幕与背景音乐 | 整片 | 拼接指纹、字幕条目与样式、叠加文字与图形、AI 角标、字体文件、背景音乐及音量、草稿 / 成片 |

规则：

1. **画面准备**：渲染时，没有画面且未锁定的镜头会自动检索或生成一次。`ai` / `image` 镜头只有在提交渲染时确认了收费（`confirm_charge`）才会调用 AI；未确认、未配置或生成失败时，有检索词就改用素材检索，仍拿不到素材才使用纯色画面。每种情况都在结果里给出提示，不中断渲染。
2. **图片镜头**：图片素材（上传或 AI 图片）渲染时加慢推镜头（Ken Burns）：先按两倍画布适配，再用 `zoompan` 从 1.0 匀速放大到 1.1 倍。
3. **锁定**：锁定的镜头不会被自动补素材，也不允许重新检索。
4. **配音失败会中断渲染**，并指出是第几个镜头。
5. 渲染期间对项目的编辑不会被覆盖：自动补的素材只合并进最新的项目文件。
6. **镜头内剪辑**：视频素材按入出点取一段（`-ss` / `-t`）。选段比镜头短时，默认先 `setpts` 慢放（最慢 0.8 倍，原声同步 `atempo`），还不够就用 `tpad` 定格最后一帧；选 `loop` 时循环播放（选段先切成临时文件再循环）。原声开启时，原声以配音为侧链做压缩（`sidechaincompress`），配音响起时自动压低，再和配音混合。
7. **转场**：有转场时，相邻镜头在交界处重叠转场时长，时间轴和拼接用同一条规则；没设时长的镜头尾部至少留出转场那么长的空白，配音不会叠在一起。音频不做交叉淡化，每个镜头的音轨按它的起点延迟后混合。任何镜头比转场还短时整片改为硬切。
8. 字幕在每个镜头的配音时段内，按标点切分、按真实字体的像素宽度换行，并尽量让两行等长。时间分配优先用配音服务返回的逐词时间戳（Edge 会返回）；服务不提供时间戳、或时间戳与文案对不上时，退回按字数比例分配。

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
| GET | `/providers`、`/voices?language=`、`/voices?provider=` | 服务状态、可选声音（Edge 按语言，其他配音服务按服务） |
| GET | `/languages` | 支持的视频语言 |
| GET / POST | `/projects/{id}/versions` | 同一视频的语言版本；POST `{"language": "en-US"}` 翻译出新版本 |
| GET / POST | `/projects` | 列出、创建项目（`mode`：`llm` / `split` / `empty`） |
| GET / PATCH / DELETE | `/projects/{id}` | 读取、修改设置、删除（渲染进行中需 `?force=true`） |
| POST | `/projects/{id}/shots` | 新增镜头（可指定插在哪个镜头之后） |
| PUT | `/projects/{id}/shots/order` | 调整镜头顺序 |
| PATCH / DELETE | `/projects/{id}/shots/{shot}` | 修改、删除镜头（含入出点、补足方式、取景、原声音量） |
| POST | `/projects/{id}/shots/{shot}/split` | 在镜头片段的第 `at` 秒处拆成两个镜头 |
| POST | `/projects/{id}/shots/{shot}/merge` | 和后一个镜头合并；同一段视频里相邻的片段连成一段 |
| POST | `/projects/{id}/footage` | 导入原片，按画面变化切成镜头（`?replace_shots=true` 替换现有镜头） |
| GET | `/projects/{id}/cost-estimate` | 渲染前的预计费用 |
| GET | `/projects/{id}/in-progress` | 正在检索或生成的镜头，页面刷新后用来恢复"生成中"状态 |
| POST | `/projects/{id}/shots/{shot}/candidates` | 为镜头检索或生成候选素材；收费生成需带 `{"confirm_charge": true}`，否则返回 402；`{"edit_selected": true}` 是改画面（把当前选中的视频交给支持视频转视频的服务） |
| POST | `/projects/{id}/bgm/candidates` | 检索背景音乐候选（只返回试听地址）；`{"query": "..."}` 留空时由大模型提炼 |
| POST | `/projects/{id}/bgm/download` | 下载选中的背景音乐整首并登记为素材 |
| POST | `/projects/{id}/assets` | 上传图片、视频或音频 |
| GET | `/projects/{id}/assets/{asset}` | 读取素材文件 |
| POST | `/projects/{id}/renders` | 提交渲染任务；`{"confirm_charge": true}` 时允许为缺画面的 AI 镜头收费生成；`{"quality": "draft"}` 是草稿档 |
| POST | `/renders/{job}/cancel` | 取消渲染（会终止正在运行的 FFmpeg） |
| GET | `/renders/{job}`、`/projects/{id}/renders` | 任务进度、历史渲染 |
| GET | `/projects/{id}/renders/{render}/video.mp4` | 下载成片（还有 `subtitles.srt`） |

错误响应的 `detail` 会按请求的 `Accept-Language` 返回中文或英文；带变量的消息（例如"镜头 2 配音失败：…"）保持原文。

渲染任务在进程内的单线程队列中执行。需要多进程或多机部署时，把 `api/jobs.py` 换成 Redis 等外部队列即可，接口不变。

## 安全约束

- FFmpeg 以参数列表调用，不经过 shell；输入限制为本地文件协议。
- 上传文件限制 500MB，先用 FFprobe 识别类型再入库；素材下载限制 500MB。
- 错误信息不包含密钥；`/providers` 不返回配置值。
- 字体只能是 `resources/fonts` 中的文件名。
- 只接受发给本机地址（或 `allowed_hosts` 里配置的地址）的请求，写请求还校验 `Origin`，防止 DNS 重绑定和跨站请求。
- 收费任务的远端 id 一拿到就写进项目（`ShotVisual.pending`），服务重启后继续轮询，不会重复提交。
