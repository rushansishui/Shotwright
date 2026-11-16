# 参与开发

先谢谢你有兴趣。这个项目以「镜头」为中心，改动前请先读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 的分层和数据模型——依赖只能自上而下（入口 → 服务 → 渲染引擎 → Provider → 核心），跨层调用基本都会被退回。

## 本地跑起来

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS / Linux：source .venv/bin/activate
pip install -e ".[dev,asr]"
cd web && npm install && npm run build && cd ..
shotwright serve --open
```

Windows 上直接双击 `start.bat` 也行，缺什么它会提示对应的 `winget` 命令。

## 提交前

```bash
pytest                 # 全部测试；渲染相关的测试需要本机有 FFmpeg
ruff check shotwright tests
ruff format shotwright tests
cd web && npm run build     # 改了前端时
```

CI 会跑 Python 3.11 / 3.12 两套测试和一次 e2e，两边都不许取消。

## 改动规范

- **一个 PR 只做一件事。** 修 bug 不要顺手改界面，加功能不要顺手重构。
- **测试跟着改。** 修 bug 先写能复现的测试；新功能至少覆盖成功路径和一条边界。
- **错误信息要能指导下一步。** 说清楚发生了什么、用户能做什么；不要只抛 `KeyError`。
- **不要引入新的必需依赖。** `pyproject.toml` 里那几条是底线；新能力做成可选的 extra。
- **不要破坏离线可用。** 不配置任何 Key 时，`shotwright new` + `shotwright render` 必须还能跑通。
- **用户数据只增不减。** 旧的 `project.json` 要能继续打开；迁移时先移文件再存项目。

## 加一家新服务

Provider 是「规格声明 + 工厂 + 协议」，步骤见 [docs/PROVIDERS.md](docs/PROVIDERS.md)。要点：

1. 在 `providers/<类别>/` 下写实现，实现对应协议；
2. 在 `registry.py` 里登记 `ProviderSpec`（收费的写 `paid=True`，字段里密钥写 `secret=True`）；
3. 在 `config.example.toml` 加一节带注释的配置；
4. 补测试（用假实现，不打真实接口）；
5. 更新 `docs/PROVIDERS.md` 里的能力表。

## 界面文字

界面文字在 `web/src/messages.ts`，中文和英文各一份，key 必须一致。服务端返回的错误提示在 `api/errors.py`，按 `Accept-Language` 选语言；带变量的消息保留原文。

## 我们大概率不会合并的改动

不是不欢迎，是为了守住范围（见 [docs/ROADMAP.md](docs/ROADMAP.md) 的「不做」）：

- 多轨自由剪辑、时间线关键帧——精剪走「导出工程文件」的路线；
- 数字人形象库、动作迁移——那是另一类产品；
- 为了加功能而引入 GPU / Redis / 常驻外部服务作为必需依赖；
- 改变 MIT 许可或给项目文件加密。

## 报告问题

用 [问题模板](.github/ISSUE_TEMPLATE/bug.yml)，里面会提示先跑 `shotwright doctor`。日志在 `storage/logs/shotwright.log`（安装版在 `%LOCALAPPDATA%\Shotwright\logs`），已自动脱敏，不含 API Key。

## 许可

提交的代码按 [MIT](LICENSE) 授权。