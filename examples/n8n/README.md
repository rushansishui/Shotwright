# Shotwright × n8n 演示

手动开始 → 创建虚构冷萃咖啡示例项目 → 提交免费样片 → 每五秒查询进度 → 输出项目、MP4 和字幕地址。

## 使用

1. 在 Shotwright 项目目录运行 `shotwright serve`，确认终端显示的服务地址。默认地址为 `http://127.0.0.1:8000`。
2. 在 n8n 的工作流菜单选择 Import from File，导入 `shotwright-sample.json`。
3. 打开“连接设置”节点，将 `baseUrl` 改为 n8n 能访问的 Shotwright 地址。地址末尾不要加 `/`。可将 `language` 改为 `en-US`。
4. 执行整个工作流。完成后打开“成片地址”节点，查看 `projectUrl`、`videoUrl` 和 `subtitlesUrl`。

## 网络连接

n8n 与 Shotwright 直接运行在同一台电脑时，使用 `http://127.0.0.1:8000`，端口按实际启动输出填写。

n8n 运行在 Docker Desktop 中、Shotwright 运行在 Windows 宿主机时，使用 `http://host.docker.internal:8000`。Shotwright 需通过 `shotwright serve --host 0.0.0.0` 监听容器可达的地址，并在 `config.toml` 的 `[app]` 中将 `host.docker.internal` 加入 `allowed_hosts`。保留已有白名单条目，并确保防火墙允许该连接。成片链接中的主机名是容器使用的地址，在电脑浏览器中可替换为 `127.0.0.1`。

n8n Cloud 需要可访问的服务地址。演示优先使用本地 n8n，远程部署需自行配置访问鉴权。

## 行为与限制

- 每次完整执行都会新建一个示例项目。写请求没有自动重试，避免请求超时后重复提交。
- 使用 `sample: true`、`confirm_charge: false` 和 `quality: draft`。现有样片模式禁止收费 AI 画面生成，示例项目自带程序绘制的插画。
- 渲染仍需要 Shotwright 的本地运行依赖，例如 FFmpeg，配音受当前 TTS 配置与网络影响。
- 失败或取消时，检查节点显示具体原因。查询达到120次后停止等待，实际渲染可能继续，可在 Shotwright 页面查看。
- HTTP 请求失败会直接结束流程。服务重启后，进程内任务状态可能丢失。已完成成片可在项目页面查找。
- 此示例用于验证接口编排。商品资料输入、审批、持久化任务与防重复请求可在后续流程中加入。

## 验证情况

已检查 JSON 结构、节点连接以及对应 Shotwright 路由。尚未在实际 n8n 实例中导入执行。
