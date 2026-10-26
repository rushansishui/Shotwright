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
│   ├── platforms.py     交付平台规格表：推荐比例、安全区、编码参数（唯一一份）
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
│   ├── storyboard.py    诉求 → 全片设定 + 文案 + 镜头（叙事结构、画面描述、运镜、衔接，出稿自检）
│   ├── prompting.py     拼装交给生成模型的完整提示词（全片设定、运镜、衔接、禁止项），按模型语言翻译
│   ├── rewrite.py       单镜头 AI 改写、短提示词和老项目一键补全
│   ├── inspect.py       看图检查：生成结果打分（产品保真）、素材排序、成片抽帧检查
│   ├── checkup.py       渲染前体检
│   ├── speech.py        配音标注：[停顿]、*强调*
│   ├── bench.py         质量基准集：固定诉求跑分镜和成片，输出可对比的指标
│   ├── visuals.py       检索（带检索缓存）/ 生成候选素材，登记上传文件，改画面，恢复收费任务
│   ├── editing.py       镜头内剪辑：拆分、合并镜头（改入出点，不切文件）
│   ├── footage.py       原片导入：按画面变化切镜，每个镜头引用原片的一段
│   ├── cost.py          费用预估与累计（参考单价）
│   ├── music.py         背景音乐：提炼检索词、检索、登记候选
│   ├── localize.py      语言版本：翻译文案和叠加文字、共用素材
│   └── durations.py     时长估算
├── media/
│   ├── ffmpeg.py        FFmpeg / FFprobe 封装（禁止网络协议）、响度测量、取帧、拼接
│   ├── subtitles.py     字幕切分、像素级换行、ASS / SRT
│   ├── grades.py        统一调色预设
│   └── beats.py         背景音乐节拍检测（纯 Python）与切点对齐
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
resources/bench/         质量基准集的固定诉求
docs/BRIEF_TEMPLATES.md  五类视频诉求模板
tests/                   pytest
```

## 数据模型

| 对象 | 说明 |
| --- | --- |
| `Project` | 一个视频的全部状态：画布（比例、帧率）、视频类型、全片设定 `look`、默认配音、字幕样式、背景音乐（闪避、淡入淡出、节拍对齐）、音效层 `sfx`、导出响度、交付平台、叠加文字与图形、转场、AI 角标、首尾帧衔接开关、主体库（参考素材）、镜头列表、素材表、费用上限与累计生成费用、修订号 |
| `Look` | 全片设定：主体列表（代号、名称、类别、外观、关联的主体库参考图）、场景、视觉风格、禁止项、统一调色预设；预留风格参考图和品牌包 |
| `Shot` | 文案（可含 `[停顿]`、`*强调*` 标注）、镜头类型 `role`、固定时长（为空则跟随配音）、画面 `ShotVisual`、可选的单独配音设置 |
| `ShotVisual` | 画面来源 `mode`（`stock` 检索 / `ai` AI 视频 / `image` AI 图片 / `upload` 上传 / `color` 纯色）、检索词、画面描述 `prompt`、静态画面描述 `image_prompt`、运镜 `camera`、衔接说明 `link`、候选素材、已选素材、多选的其他素材 `sequence`（依次播放）、首帧、生成版本记录 `takes`、锁定标记、未取回的收费任务 `pending`，以及剪辑设置（见下） |
| `Take` | 一次生成的版本记录：素材、类别（视频 / 图片 / 关键帧 / 改画面 / 续生成）、批次、生成时的画面描述、看图检查得分和问题 |
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
| 配音 | 每个镜头 | 文案（含停顿、强调标注）、配音服务、声音、语速、音量 |
| 镜头渲染 | 每个镜头 | 配音指纹、时长、所选素材的 SHA-256（或纯色）、适配方式与取景、选段与补足方式、原声音量、图片运动效果版本、画布、草稿 / 成片 |
| 拼接 | 整片 | 所有镜头渲染指纹及顺序、转场类型与时长 |
| 字幕与声音 | 整片 | 拼接指纹、字幕条目与样式、叠加文字与图形、AI 角标、字体文件、背景音乐及其设置、音效层与切点、导出响度、统一调色、交付平台、草稿 / 成片 |

规则：

1. **画面准备**：渲染时，没有画面且未锁定的镜头会自动检索或生成一次。`ai` / `image` 镜头只有在提交渲染时确认了收费（`confirm_charge`）才会调用 AI；未确认、未配置或生成失败时，有检索词就改用素材检索，仍拿不到素材才使用纯色画面。每种情况都在结果里给出提示，不中断渲染。
2. **图片镜头**：图片素材（上传或 AI 图片）渲染时加慢推镜头（Ken Burns）：先按两倍画布适配，再用 `zoompan` 从 1.0 匀速放大到 1.1 倍。
3. **锁定**：锁定的镜头不会被自动补素材，也不允许重新检索。
4. **配音失败会中断渲染**，并指出是第几个镜头。
5. 渲染期间对项目的编辑不会被覆盖：自动补的素材只合并进最新的项目文件。
6. **镜头内剪辑**：视频素材按入出点取一段（`-ss` / `-t`）。选段比镜头短时，默认先 `setpts` 慢放（最慢 0.8 倍，原声同步 `atempo`），还不够就用 `tpad` 定格最后一帧；选 `loop` 时循环播放（选段先切成临时文件再循环）。原声开启时，原声以配音为侧链做压缩（`sidechaincompress`），配音响起时自动压低，再和配音混合。
7. **转场**：有转场时，相邻镜头在交界处重叠转场时长，时间轴和拼接用同一条规则；没设时长的镜头尾部至少留出转场那么长的空白，配音不会叠在一起。音频不做交叉淡化，每个镜头的音轨按它的起点延迟后混合。任何镜头比转场还短时整片改为硬切。
8. 字幕在每个镜头的配音时段内，按标点切分、按真实字体的像素宽度换行，并尽量让两行等长。时间分配优先用配音服务返回的逐词时间戳（Edge 会返回）；服务不提供时间戳、或时间戳与文案对不上时，退回按字数比例分配。
9. **多选素材**：镜头里选了几段素材时（`selected` + `sequence`，最多 6 段），按各段可用长度分配镜头时长，每段单独渲染成无声画面后依次拼接，再混入配音；第一段沿用入出点和取景，其他段用整段素材、居中取景，多选时不混原声。
10. **声音**：背景音乐以配音（含原声）为侧链压缩，说话时自动压低；开头淡入、结尾淡出。环境声循环铺满整片，转场音效在每个切点前 0.25 秒播放。混音之后用两遍 loudnorm（线性模式）把整片统一到 -14 LUFS、真峰值 -1.5 dBTP，静音的片子不处理。
11. **配音标注**：文案里的 `[停顿]` / `[停顿 1.2]` 和 `*强调*` 会让这个镜头分段合成：停顿处插入静音，强调的部分语速放慢一成、音量提高两成、前面留 0.12 秒；字幕和时长估算都去掉标注。
12. **节拍对齐**（可选）：检测背景音乐的拍点，把镜头切换点向后挪到 0.35 秒内最近的拍点，只延长镜头，手动设了时长的镜头不动。
13. **统一调色与交付**：调色在字幕和叠加文字之前做；选了交付平台时，成片档按平台参数重新编码（H.264 High、码率上限），字幕和叠加文字的边距挪进平台安全区。
14. **成片抽帧检查**：配置了看图模型时，成片档渲染后每个镜头取一帧检查手指、面部、乱码文字等瑕疵，结果写进 `render.json` 并作为提示显示。

## 从诉求到画面

视频质量的大部分取决于「从诉求到画面」这一段，处理流程：

1. **诉求**：首页先选视频类型（商品广告、品牌宣传、知识科普、口播、原片改造），按类型给模板和例子（见 `docs/BRIEF_TEMPLATES.md`），提交前检查没填的占位、诉求太短、没写外观。
2. **写分镜**（`services/storyboard.py`）：把视频类型、画面比例、目标时长、主体库说明和诉求原文交给大模型。先定全片设定（主体代号与外观、场景、风格、禁止项、调色），再按视频类型的叙事结构写镜头：每个镜头有镜头类型、文案、检索词、画面描述（中文 120–200 字 / 英文 60–120 词）、静态画面描述、运镜和衔接说明。出稿后自检（时长、缺主体、描述过短、缺衔接、未定义代号），不合格时带着问题重写一次。按镜头类型选默认画面来源。
3. **拼提示词**（`services/prompting.py`）：镜头里只存这个镜头本身的描述；生成时按「镜头描述 → 运镜 → 衔接 → 用到的主体外观 → 场景与风格 → 主体库参考要求 → 禁止项（含不出现文字）」拼出完整提示词。提示词跟随项目语言（中文项目用中文，其他用英文），模型不支持时提交前用大模型翻译（Provider 的 `prompt_languages`）。
4. **生成**（`services/visuals.py`）：可以先出关键帧再图生视频、一次生成多条、按草稿 / 成片分辨率生成；首尾帧衔接（可选）用上一个 AI 镜头的最后一帧作首帧；配音超过模型一段的上限时分段续生成再拼接。每次生成记一条版本（`Take`），收费前检查费用上限。
5. **检查**（`services/inspect.py`，需要看图模型）：生成结果按切题程度和主体一致性打分，多条时选最好的；素材库候选按切题排序、标出水印和文字；成片档渲染后抽帧找瑕疵。
6. **改写与补全**（`services/rewrite.py`）：单镜头按一句要求改写，老项目和短提示词一键补全，都不改文案、不收费。
7. **渲染前体检**（`services/checkup.py`）：只读检查，渲染面板列出问题。

质量基准集（`shotwright bench`）用 `resources/bench/briefs.json` 里固定的五条诉求、横竖屏各跑一次，输出画面描述长度、衔接覆盖率、全片设定、自检剩余问题、响度、慢放定格占比、纯色占比等指标，并与上一次对比。

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

每个大模型都可以另填 `vision_model`（看图模型）：填了之后生成结果、素材候选和成片会多一道看图检查；留空时不检查。

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
| POST | `/projects/{id}/duplicate` | 复制成新项目继续编辑（素材、设置、镜头和渲染缓存一起复制，不含历史成片和累计费用）；命令行 `shotwright copy` |
| POST | `/projects/{id}/shots` | 新增镜头（可指定插在哪个镜头之后） |
| PUT | `/projects/{id}/shots/order` | 调整镜头顺序 |
| PATCH / DELETE | `/projects/{id}/shots/{shot}` | 修改、删除镜头（含入出点、补足方式、取景、原声音量） |
| POST | `/projects/{id}/shots/{shot}/split` | 在镜头片段的第 `at` 秒处拆成两个镜头 |
| POST | `/projects/{id}/shots/{shot}/merge` | 和后一个镜头合并；同一段视频里相邻的片段连成一段 |
| POST | `/projects/{id}/footage` | 导入原片，按画面变化切成镜头（`?replace_shots=true` 替换现有镜头） |
| GET | `/projects/{id}/cost-estimate` | 渲染前的预计费用（`?quality=draft` 按草稿分辨率估算） |
| GET | `/projects/{id}/checkup` | 渲染前体检：缺画面、素材太短、分辨率不足、文字重叠、超出费用上限、画面比例不合平台等 |
| GET | `/platforms` | 交付平台规格（推荐比例、安全区） |
| POST | `/projects/{id}/shots/{shot}/rewrite` | 单镜头 AI 改写：`{"instruction": "..."}`，只改画面描述、运镜和衔接 |
| POST | `/projects/{id}/expand` | 一键补全：扩写过短的画面描述、补衔接，没有全片设定时一并生成 |
| POST | `/projects/{id}/sfx/ambience` | 自动配环境声：按镜头检索词从音乐库找一段环境声 |
| GET | `/projects/{id}/in-progress` | 正在检索或生成的镜头，页面刷新后用来恢复"生成中"状态 |
| POST | `/projects/{id}/shots/{shot}/candidates` | 为镜头检索或生成候选素材；收费生成需带 `{"confirm_charge": true}`，否则返回 402；`{"edit_selected": true}` 是改画面（把当前选中的视频交给支持视频转视频的服务）；`count`（1–3）一次生成几条、`keyframes`（2–3）先出关键帧、`quality`（`draft` / `final`）生成分辨率 |
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
