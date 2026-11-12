# 第三方声明

## MoneyPrinterTurbo

Shotwright 的部分设计和实现参考了 MoneyPrinterTurbo：

- 仓库：<https://github.com/harry0703/MoneyPrinterTurbo>
- 许可证：MIT License，Copyright (c) 2024 Harry

参考的部分：

| Shotwright 文件 | 参考的 MoneyPrinterTurbo 代码 |
| --- | --- |
| `shotwright/providers/registry.py` | `app/services/video_providers/registry.py` 的显式注册、惰性导入设计 |
| `shotwright/providers/stock/pexels.py` | `app/services/material.py` 中 `search_videos_pexels` 的检索与清晰度挑选逻辑 |
| `shotwright/providers/tts/edge.py` | `app/services/voice.py` 中的 Edge TTS 用法 |
| `shotwright/pipeline/render.py` | `app/services/video_project.py` 的按镜头指纹增量渲染思路 |
| `shotwright/providers/paid_task.py` | `app/services/video_providers/client.py`、`polling.py` 的收费任务提交与轮询语义 |
| `shotwright/providers/video_gen/seedance.py` | `app/services/video_providers/seedance.py` 的请求格式 |
| `shotwright/services/visuals.py`（收费确认） | `app/services/ai_video_studio.py` 的扣费确认设计 |

MoneyPrinterTurbo 的许可声明如下：

```
MIT License

Copyright (c) 2024 Harry

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 字体

`resources/fonts/NotoSansSC-Bold.ttf` 来自 Google Noto CJK（<https://github.com/notofonts/noto-cjk>），
使用 SIL Open Font License 1.1，许可全文见 `resources/fonts/OFL-NotoSansSC.txt`。
为兼容字幕渲染，原始 OTF 已转换为 TrueType 轮廓，字形未做修改。

`resources/fonts/NotoSansKR-Bold.ttf`（韩文）与 `resources/fonts/NotoSansThai-Bold.ttf`（泰文）来自
Google Fonts（<https://github.com/google/fonts>，`ofl/notosanskr`、`ofl/notosansthai`），使用 SIL Open Font
License 1.1，许可全文分别见 `resources/fonts/OFL-NotoSansKR.txt`、`resources/fonts/OFL-NotoSansThai.txt`。
为方便字幕渲染，从可变字体中导出了 Bold（字重 700）静态实例，字形未做修改。

其他字体同样使用 SIL Open Font License 1.1，字形未做修改：

| 字体文件 | 来源 | 许可全文 |
| --- | --- | --- |
| `NotoSansSC-Regular.otf`、`NotoSerifSC-Bold.otf` | Google Noto CJK（<https://github.com/notofonts/noto-cjk>） | `resources/fonts/OFL-NotoSansSC.txt` |
| `MaShanZheng-Regular.ttf`（马善政毛笔楷书） | <https://github.com/googlefonts/mashanzheng>，Copyright 2018 The Ma Shan Zheng Project Authors | `resources/fonts/OFL-MaShanZheng.txt` |
| `ZCOOLKuaiLe-Regular.ttf`（站酷快乐体） | <https://github.com/googlefonts/zcool-kuaile>，Copyright 2018 The ZCOOL KuaiLe Project Authors | `resources/fonts/OFL-ZCOOLKuaiLe.txt` |
| `ZCOOLQingKeHuangYou-Regular.ttf`（站酷庆科黄油体） | <https://github.com/googlefonts/zcool-qingke-huangyou>，Copyright 2018 The ZCOOL QingKe HuangYou Project Authors | `resources/fonts/OFL-ZCOOLQingKeHuangYou.txt` |

## Windows 安装包里附带的程序

### FFmpeg

Windows 安装包在 `ffmpeg\` 目录里附带了 FFmpeg 的 `ffmpeg.exe` 和 `ffprobe.exe`，Shotwright 以独立程序的方式调用它们，
没有修改 FFmpeg。

- 构建：gyan.dev 的 essentials 构建（<https://www.gyan.dev/ffmpeg/builds/>），版本号见 `scripts/build-installer.ps1`
  里的 `$FfmpegVersion`
- 许可证：GNU General Public License v3（该构建启用了 GPL 组件），许可全文随安装包放在 `ffmpeg\FFmpeg-LICENSE.txt`
- 源码：`ffmpeg\FFmpeg-README.txt` 写有这个构建对应的 FFmpeg 源码提交和所用的第三方库；FFmpeg 源码见
  <https://github.com/FFmpeg/FFmpeg>、<https://ffmpeg.org/download.html>

从源码运行 Shotwright 时使用你自己安装的 FFmpeg，不涉及上述分发。

### Python 运行库和第三方库

安装包用 PyInstaller 打包，内含 Python 运行库以及 FastAPI、Uvicorn、Pydantic、HTTPX、edge-tts、Pillow、fontTools、
faster-whisper、CTranslate2、ONNX Runtime 等第三方库。它们各自的名称、版本、许可证和许可全文由打包脚本自动生成，
放在安装目录的 `THIRD_PARTY_LICENSES.txt`。faster-whisper 自带的语音活动检测模型来自 Silero VAD
（<https://github.com/snakers4/silero-vad>，MIT License）。

### 网页界面

网页界面使用 React 和 React DOM（<https://github.com/facebook/react>，MIT License，Copyright (c) Meta Platforms, Inc.
and affiliates），由 Vite 打包进 `web/dist`。

## 素材

通过 Pixabay、Pexels 等素材库下载的视频遵循各自平台的许可协议。AI 生成的画面遵循对应服务商的使用条款，Shotwright 会在素材信息里记录作者与来源页面。
