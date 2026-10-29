# 多阶段构建：先在 Node 里构建网页界面，再在 Python 镜像里运行服务。
FROM node:20-alpine AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

# FFmpeg 是渲染的硬依赖；字体放在 resources/fonts 里随镜像分发。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SHOTWRIGHT_STORAGE=/data/projects \
    SHOTWRIGHT_CONFIG=/data/config.toml

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE.md ./
COPY shotwright/ ./shotwright/
COPY resources/ ./resources/
# 用可编辑安装：代码留在 /app，ROOT_DIR 才能找到 resources/fonts 和 web/dist。
RUN pip install --no-cache-dir -e ".[asr]"

COPY --from=web /app/web/dist ./web/dist

# 数据目录：项目、渲染结果都持久化在挂载卷里。
RUN mkdir -p /data/projects
VOLUME ["/data"]
EXPOSE 8000

# 容器内必须监听 0.0.0.0 才能从宿主机访问；Host 校验在应用层完成。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v2/health', timeout=3)"

CMD ["shotwright", "serve", "--host", "0.0.0.0", "--port", "8000"]
