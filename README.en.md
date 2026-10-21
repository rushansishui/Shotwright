# Shotwright

[简体中文](README.md) | English

**From a video brief to a finished video: AI writes the script, each shot gets generated or stock footage, voice-over and subtitles are added automatically, and every shot can be adjusted on its own.**

Shotwright is built around the *shot*. The script is split into shots, and each shot picks its own visual source (stock search, AI generation, or your own uploaded image or video). Any shot can be replaced, locked or redone by itself. Change one shot and only that shot is re-rendered, plus the final assembly.

> Current version: v0.3.0 (in development). See the [roadmap](docs/ROADMAP.md).

## Features

- **Storyboard editing**: split a script into shots sentence by sentence, or write a video brief (audience, tone, key points; up to 4096 bytes), pick a length, and let an LLM write the script, decide how many shots it needs, and suggest search terms and visual prompts for each shot.
- **Mixed visuals**: in one video, some shots can use Pixabay / Pexels footage, some AI video (Seedance) or AI images (Seedream with a slow Ken Burns push-in), and some your own uploads. An AI image can also serve as the first frame of an AI video.
- **Charge confirmation**: every paid generation must be confirmed in the UI before it is submitted. If the result of a submission can't be confirmed, it is never retried automatically, so you are never charged twice.
- **Incremental rendering**: each stage is fingerprinted by its inputs and cached, so unchanged shots are reused. Renders can be cancelled at any time.
- **Voice-over and subtitles**: free Edge online voices; subtitles wrap by real font width with balanced line lengths.
- **Three ways in**: web UI, REST API (`/api/v2`) and CLI, all sharing the same services and render queue.
- **Multilingual**: videos can be in 16 languages, including Chinese, English, Japanese, Korean and Spanish, with a matching voice and subtitle font picked automatically. One click translates a video into another language version (visuals are shared; only the script, voice and subtitles change). The web UI switches between 中文 and English.
- **Logs**: `storage/logs/shotwright.log` records the model, task id and result of every paid task, plus each generation and render. Logs rotate daily, are kept for 14 days, and API keys are masked.
- **Works offline**: with no services configured it still runs end to end (silent voice-over and solid-color frames), handy for tuning storyboard pacing first.

## Quick start

You need Python 3.11+, [FFmpeg](https://ffmpeg.org/download.html) (with libass, which most builds include) and Node.js 20+ (only for building the web UI).

```bash
# 1. Install the backend
python -m venv .venv
.venv\Scripts\activate          # macOS / Linux: source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure (optional): copy the template and fill in Pixabay and LLM API keys
copy config.example.toml config.toml   # macOS / Linux: cp

# 3. Build the web UI
cd web && npm install && npm run build && cd ..

# 4. Start
shotwright serve
```

Then open <http://127.0.0.1:8000>. On Windows you can simply double-click `start.bat` to start and `restart.bat` to restart (it first stops the old server on port 8000). For frontend development, run `cd web && npm run dev` in another terminal and visit <http://localhost:5173>.

### Command line

```bash
shotwright new --script-file script.txt --aspect 9:16      # split a script into shots
shotwright new --subject "How AI is changing daily life" --llm --language en-US --duration 45
                                                          # LLM writes a ~45 s English script and storyboard
shotwright show <project-id>
shotwright shot <project-id> 2 --text "New line" --terms "city night,traffic"
shotwright render <project-id>                            # only redo shots that changed
shotwright render <project-id> --allow-paid               # allow paid AI generation for shots missing visuals
shotwright translate <project-id> ja-JP                   # create a Japanese version (new project, shared visuals)
```

## Configuration

See [config.example.toml](config.example.toml) (comments are in Chinese). Every provider field can also be overridden with an environment variable named `SHOTWRIGHT_<KIND>_<ID>_<FIELD>`, for example:

```bash
SHOTWRIGHT_STOCK_PIXABAY_API_KEY=xxxx
SHOTWRIGHT_DEFAULT_LLM=deepseek
SHOTWRIGHT_LLM_DEEPSEEK_API_KEY=sk-xxxx
```

Four LLM presets are built in: Doubao Seed, DeepSeek, Kimi and Qwen. Pick one and fill in its API key. Any other OpenAI-compatible API can be set up in the `openai` section with your own base URL and model.

AI video (Seedance) and AI images (Seedream) run on Volcengine Ark, the same platform as Doubao. Once the key in `[providers.llm.doubao]` is filled in and the models are enabled in the Ark console, they work without entering the key again.

Keys live only in the config file or environment variables and are never written into project files, so projects are safe to share.

## Documentation

These documents are currently in Chinese.

- [Architecture](docs/ARCHITECTURE.md): layers, data model, render pipeline, provider plugins
- [Roadmap](docs/ROADMAP.md)
- [Porting notes from MoneyPrinterTurbo](docs/PORTING.md)
- [Adding a provider](docs/PROVIDERS.md)

## Acknowledgements

Shotwright is written from scratch. Some of its design and implementation draw on [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) (MIT License, © 2024 Harry); see [NOTICE.md](NOTICE.md) for details.

## License

[MIT](LICENSE). The bundled Noto Sans SC / KR / Thai fonts are licensed under the SIL Open Font License 1.1 (see [resources/fonts](resources/fonts)).
