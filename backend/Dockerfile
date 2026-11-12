# VitaFlow 后端镜像：FastAPI + MySQL + Chromium/WeasyPrint + 中文 OCR。
# PDF 导出默认走 Playwright 自带 Chromium；WeasyPrint 作为 auto 模式兜底。
FROM python:3.11.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000

WORKDIR /app

# Debian 源与 PyPI 默认走国内镜像；海外构建可覆盖：
#   docker compose build --build-arg APT_MIRROR=https://deb.debian.org backend
ARG APT_MIRROR=https://mirrors.aliyun.com
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# 系统级依赖（pip 不会安装）：WeasyPrint 的 Pango/Cairo、中文字体、Tesseract 中文 OCR。
# 仓库内 backend/app/static/fonts 只有 README，字体实际依赖系统安装，Linux 必须带 fonts-noto-cjk。
RUN set -eux; \
    sed -i "s|http://deb.debian.org/debian-security|${APT_MIRROR}/debian-security|g; \
            s|http://deb.debian.org/debian|${APT_MIRROR}/debian|g" \
        /etc/apt/sources.list.d/debian.sources; \
    apt-get update -o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30; \
    apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libharfbuzz-subset0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
        fonts-noto-cjk \
        tesseract-ocr \
        tesseract-ocr-chi-sim; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt -i "${PIP_INDEX_URL}"

# Chromium 及 Playwright 运行所需的系统库，单独成层便于缓存。
RUN python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 8000

# 显式 --log-level：容器 env_file 会注入 UVICORN_LOG_LEVEL=INFO，而 uvicorn 的
# Click CLI 读取 UVICORN_* 时只接受小写（大写会直接启动失败）。应用内日志级别仍由
# settings.uvicorn_log_level 在 configure_logging() 中生效。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
