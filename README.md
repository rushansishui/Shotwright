# Shotwright 镜匠

**从一句话到成片：AI 写文案、按分镜生成或检索画面、自动配音和字幕，每个镜头都可以单独调整。**

Shotwright 以"镜头"为核心：文案被拆成一个个镜头，每个镜头可以单独选择画面来源（素材库检索、AI 生成、上传图片或视频），可以替换、锁定、单独重做。改一个镜头，只会重新渲染这个镜头，再加上最后的拼接导出。

> 当前版本：v0.1.0（开发中）。路线图见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 功能

- **分镜编辑**：按句子拆分镜头，或者让大模型根据主题写文案、拆镜头，并给出检索词和画面提示词。
- **画面混用**：同一个视频里，有的镜头用 Pixabay / Pexels 素材，有的用 AI 生成，有的用自己上传的图片或视频。
- **增量渲染**：每个阶段按输入计算指纹并缓存，没改的镜头直接复用。
- **配音与字幕**：免费的 Edge 在线语音；字幕按真实字体宽度自动换行，行长均衡。
- **三种入口**：网页界面、REST API（`/api/v2`）、命令行，共用同一套服务和渲染队列。
- **离线可用**：不配置任何服务也能跑通（静音配音 + 纯色画面），方便先调分镜节奏。

## 快速开始

需要 Python 3.11+、[FFmpeg](https://ffmpeg.org/download.html)（需带 libass，常见发行版都带）和 Node.js 20+（仅构建网页界面时需要）。

```bash
# 1. 安装后端
python -m venv .venv
.venv\Scripts\activate          # macOS / Linux：source .venv/bin/activate
pip install -e ".[dev]"

# 2. 配置（可选）：复制模板后填写 Pixabay、大模型的 API Key
copy config.example.toml config.toml   # macOS / Linux：cp

# 3. 构建网页界面
cd web && npm install && npm run build && cd ..

# 4. 启动
shotwright serve
```

打开 <http://127.0.0.1:8000> 即可使用。开发前端时可以另开终端运行 `cd web && npm run dev`，访问 <http://localhost:5173>。

### 命令行

```bash
shotwright new --script-file 文案.txt --aspect 9:16       # 按句拆成镜头
shotwright new --subject "人工智能如何改变日常生活" --llm   # 让大模型写文案并拆镜头
shotwright show <项目id>
shotwright shot <项目id> 2 --text "新的文案" --terms "city night,traffic"
shotwright render <项目id>                                # 只重做有改动的镜头
```

## 配置

见 [config.example.toml](config.example.toml)。所有 Provider 字段也可以用环境变量覆盖，格式为 `SHOTWRIGHT_<类别>_<ID>_<字段>`，例如：

```bash
SHOTWRIGHT_STOCK_PIXABAY_API_KEY=xxxx
SHOTWRIGHT_LLM_OPENAI_API_KEY=sk-xxxx
SHOTWRIGHT_LLM_OPENAI_BASE_URL=https://api.deepseek.com/v1
SHOTWRIGHT_LLM_OPENAI_MODEL=deepseek-chat
```

密钥只保存在配置文件或环境变量中，不会写进项目文件，项目文件可以放心分享。

## 文档

- [架构说明](docs/ARCHITECTURE.md)：分层、数据模型、渲染流水线、Provider 插件
- [路线图](docs/ROADMAP.md)
- [参考 MoneyPrinterTurbo 的移植清单](docs/PORTING.md)
- [如何新增一个 Provider](docs/PROVIDERS.md)

## 致谢

Shotwright 是一个全新编写的项目，部分设计和实现参考了 [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo)（MIT License，© 2024 Harry），具体见 [NOTICE.md](NOTICE.md)。

## 许可证

[MIT](LICENSE)。内置字体 Noto Sans SC 使用 [SIL Open Font License 1.1](resources/fonts/OFL-NotoSansSC.txt)。
