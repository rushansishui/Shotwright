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
│ Provider   providers/{llm,tts,stock,video_gen}/  + registry.py       │
├──────────────────────────────────────────────────────────────┤
│ 核心       core/models.py   core/store.py   core/fingerprint.py      │
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
│   ├── llm/openai_compat.py
│   ├── tts/edge.py      Edge 在线语音（免费）
│   ├── tts/silent.py    静音配音（离线预览、测试）
│   ├── stock/pixabay.py
│   └── stock/pexels.py
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
| `ShotVisual` | 画面来源 `mode`（`stock` 检索 / `ai` 生成 / `upload` 上传 / `color` 纯色）、检索词、提示词、候选素材、已选素材、锁定标记 |
| `Asset` | 落盘的媒体文件：类型、来源、SHA-256、尺寸与时长、作者和授权信息 |

存储结构：

```text
storage/projects/<项目id>/
├── project.json      项目状态（原子替换写入）
├── assets/           <sha256 前 16 位>.<扩展名>
├── cache/            voice/ shots/ assembly/ final/，按指纹命名，可随时删除
└── renders/<渲染id>/ video.mp4、subtitles.srt、render.json
```

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
| 镜头渲染 | 每个镜头 | 配音指纹、时长、所选素材的 SHA-256（或纯色）、适配方式、画布 |
| 拼接 | 整片 | 所有镜头渲染指纹及顺序 |
| 字幕与背景音乐 | 整片 | 拼接指纹、字幕条目与样式、字体文件、背景音乐及音量 |

规则：

1. **画面准备**：渲染时，没有画面且未锁定的 `stock` / `ai` 镜头会自动检索或生成一次；失败时使用纯色画面并在结果里给出提示，不中断渲染。
2. **锁定**：锁定的镜头不会被自动补素材，也不允许重新检索。
3. **配音失败会中断渲染**，并指出是第几个镜头。
4. 渲染期间对项目的编辑不会被覆盖：自动补的素材只合并进最新的项目文件。
5. 字幕在每个镜头的配音时段内，按标点切分、按字数比例分配时间，按真实字体的像素宽度换行，并尽量让两行等长。

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

新增一家服务的步骤见 [PROVIDERS.md](PROVIDERS.md)。

## API

所有接口在 `/api/v2` 下，完整文档见运行时的 `/docs`。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/providers`、`/voices` | 服务状态、可选声音 |
| GET / POST | `/projects` | 列出、创建项目（`mode`：`llm` / `split` / `empty`） |
| GET / PATCH / DELETE | `/projects/{id}` | 读取、修改设置、删除 |
| POST | `/projects/{id}/shots` | 新增镜头（可指定插在哪个镜头之后） |
| PUT | `/projects/{id}/shots/order` | 调整镜头顺序 |
| PATCH / DELETE | `/projects/{id}/shots/{shot}` | 修改、删除镜头 |
| POST | `/projects/{id}/shots/{shot}/candidates` | 为镜头检索或生成候选素材 |
| POST | `/projects/{id}/assets` | 上传图片、视频或音频 |
| GET | `/projects/{id}/assets/{asset}` | 读取素材文件 |
| POST | `/projects/{id}/renders` | 提交渲染任务 |
| GET | `/renders/{job}`、`/projects/{id}/renders` | 任务进度、历史渲染 |
| GET | `/projects/{id}/renders/{render}/video.mp4` | 下载成片（还有 `subtitles.srt`） |

渲染任务在进程内的单线程队列中执行。需要多进程或多机部署时，把 `api/jobs.py` 换成 Redis 等外部队列即可，接口不变。

## 安全约束

- FFmpeg 以参数列表调用，不经过 shell；输入限制为本地文件协议。
- 上传文件限制 500MB，先用 FFprobe 识别类型再入库；素材下载限制 500MB。
- 错误信息不包含密钥；`/providers` 不返回配置值。
- 字体只能是 `resources/fonts` 中的文件名。
