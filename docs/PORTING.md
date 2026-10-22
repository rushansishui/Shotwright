# 参考 MoneyPrinterTurbo 的移植清单

Shotwright 是全新编写的项目，MoneyPrinterTurbo（下称 MPT，MIT License）作为参考。移植任何代码时：

1. 按 Shotwright 的接口重写，不直接复制大段代码；
2. 必须复制时，在文件头注明来源文件，并在 [NOTICE.md](../NOTICE.md) 的表格里登记；
3. 连同 MPT 中对应的测试用例一起移植或改写。

## 已参考

| Shotwright | MPT | 说明 |
| --- | --- | --- |
| `providers/registry.py` | `app/services/video_providers/registry.py` | 显式注册、惰性导入的设计 |
| `providers/stock/pexels.py` | `app/services/material.py` → `search_videos_pexels` | 检索参数、挑选最小可用清晰度 |
| `providers/tts/edge.py` | `app/services/voice.py` → Edge TTS 部分 | 语速、音量参数转换 |
| `pipeline/render.py` | `app/services/video_project.py` → `stage_graph` | 按镜头指纹增量渲染的思路 |
| `providers/paid_task.py` | `app/services/video_providers/client.py`、`polling.py` | 收费任务提交不重试、轮询退避与状态集合 |
| `providers/video_gen/seedance.py` | `app/services/video_providers/seedance.py` | 方舟任务接口的请求格式、首帧参数 |
| `services/visuals.py` 收费确认 | `app/services/ai_video_studio.py` → `AIVideoChargeConfirmationRequired` | 未确认时拒绝提交 |

## 已自行实现（未复制 MPT 代码）

以下功能原列在待移植清单里，v0.3 已按 Shotwright 的接口和各服务的官方文档另行实现：

| 功能 | Shotwright 位置 | MPT 对应位置 |
| --- | --- | --- |
| Azure / 硅基流动 / MiniMax / ElevenLabs 配音 | `providers/tts/` | `app/services/voice.py` |
| 逐词时间戳字幕（Edge） | `TTSProvider` 返回值、`media/subtitles.py` | `voice.py` 中 SubMaker 相关处理 |
| Coverr 素材库（免 Key 接口） | `providers/stock/coverr.py` | `material.py` → `search_videos_coverr` |
| 素材检索缓存（按项目、7 天） | `services/visuals.py` | `app/services/material_cache.py` |
| 背景音乐（改为 Internet Archive 检索，按授权过滤） | `providers/music/archive.py`、`services/music.py` | `app/services/elevenlabs_music.py`、`sonilo.py`（AI 生成音乐） |

## 待移植

| 功能 | MPT 位置 | 目标位置 | 备注 |
| --- | --- | --- | --- |
| 更多 AI 视频服务 | `app/services/video_providers/sources/*`（Wavespeed、Muapi 等） | `providers/video_gen/` | 计划改由 v0.5 的聚合 Provider（Runway API / fal.ai）覆盖；参考素材（`ReferenceAsset`）由 v0.4 的主体库替代 |
| AI 生成背景音乐（可选） | `app/services/elevenlabs_music.py`、`sonilo.py` | `providers/music/` | 目前用检索；需要原创配乐时再考虑 |

## 不移植

- Streamlit 界面（`webui/`）：已用 React 重写。
- 原项目的赞助商资源、版本检查、内置背景音乐与商业字体。
- `/api/v1` 接口：Shotwright 只提供以项目为中心的 `/api/v2`。
