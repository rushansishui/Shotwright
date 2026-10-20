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

## 待移植（按优先级）

| 功能 | MPT 位置 | 目标位置 | 备注 |
| --- | --- | --- | --- |
| 更多 AI 视频服务 | `app/services/video_providers/sources/*`（Wavespeed、Muapi 等） | `providers/video_gen/` | 复用 `providers/paid_task.py`；参考素材（`ReferenceAsset`）待补 |
| Azure / 硅基流动 / MiniMax / ElevenLabs 配音 | `app/services/voice.py` 中的 `azure_tts_v2`、`siliconflow_tts`、`minimax_tts`、`elevenlabs_tts` | `providers/tts/` | 每家一个文件；远程音频校验逻辑见 `_validate_remote_tts_audio` |
| 逐词时间戳字幕 | `voice.py` 中 SubMaker 相关处理 | `TTSProvider` 返回值增加时间戳 | 用于字幕精确对齐 |
| Coverr | `material.py` → `search_videos_coverr` | `providers/stock/` | Pixabay 已按官方文档另行实现 |
| 素材缓存 | `app/services/material_cache.py` | `services/visuals.py` | 相同检索词复用下载结果 |
| AI 背景音乐 | `app/services/elevenlabs_music.py`、`sonilo.py` | `providers/music/` | |

## 不移植

- Streamlit 界面（`webui/`）：已用 React 重写。
- 原项目的赞助商资源、版本检查、内置背景音乐与商业字体。
- `/api/v1` 接口：Shotwright 只提供以项目为中心的 `/api/v2`。
