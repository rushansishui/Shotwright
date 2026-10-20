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

## 素材

通过 Pexels 等素材库下载的视频遵循各自平台的许可协议，Shotwright 会在素材信息里记录作者与来源页面。
