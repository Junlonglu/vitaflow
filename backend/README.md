# ResumeFlow Backend

FastAPI 后端提供认证、简历管理、HTML 预览、PDF 导出、头像上传和 AI 对话代理接口。

## 启动

PDF 导出默认优先使用 Playwright + Chromium 渲染，速度和浏览器预览一致性通常都优于 WeasyPrint。WeasyPrint 仍作为兜底渲染器保留，因此建议同时安装中文字体和兜底系统库。

Debian/Ubuntu：

```bash
sudo apt-get update
sudo apt-get install -y \
  libpango-1.0-0 \
  libpangoft2-1.0-0 \
  libcairo2 \
  libgdk-pixbuf-2.0-0 \
  libffi-dev \
  shared-mime-info \
  fonts-noto-cjk
```

CentOS/RHEL：

```bash
sudo yum install -y \
  pango \
  cairo \
  gdk-pixbuf2 \
  libffi \
  shared-mime-info \
  google-noto-sans-cjk-fonts
```

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

## Docker

```bash
# 只构建后端镜像（含 Chromium、WeasyPrint 系统库、中文字体、Tesseract 中文 OCR）
docker compose build backend

# 全栈启动（backend-migrate → backend → frontend）
docker compose --profile full up -d --build
```

- 镜像构建使用 `constraints.txt` 锁定依赖版本。
- compose 会把容器内 `PDF_CHROMIUM_EXECUTABLE_PATH` 置空（改用 Playwright 自带浏览器），并将 `EXPORT_DIR` / `UPLOAD_DIR` 固定为相对路径。
- 全栈模式下容器通过服务名访问 MySQL / Redis / MinIO / ai-service，无需修改 `.env` 中的 127.0.0.1 值。

## 日志配置

后端启动时会初始化统一日志配置，默认输出到控制台，并记录应用启动、关闭、请求耗时和未处理异常堆栈。

```env
LOG_LEVEL=INFO
UVICORN_LOG_LEVEL=INFO
SQLALCHEMY_LOG_LEVEL=WARNING
LOG_ACCESS_ENABLED=true
LOG_FILE=
LOG_FILE_MAX_BYTES=10485760
LOG_FILE_BACKUP_COUNT=5
```

常用日志级别为 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。如果需要落盘，将 `LOG_FILE` 设置为相对后端目录或绝对路径，例如 `LOG_FILE=logs/backend.log`；日志文件会按 `LOG_FILE_MAX_BYTES` 和 `LOG_FILE_BACKUP_COUNT` 自动轮转。

如果服务器不能使用 Playwright 自动安装浏览器，也可以安装系统 Chromium，并配置：

```env
PDF_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
```

macOS 本地开发建议保持 `PDF_CHROMIUM_EXECUTABLE_PATH` 为空，让 Playwright 使用自带 headless Chromium；如果配置 `/Applications/Google Chrome.app/...`，导出时可能会显示 Chrome App。

`PDF_RENDERER=chromium` 会严格使用 Chromium 渲染 PDF，失败时直接报错，便于发现浏览器未安装或路径错误；如果要允许 Chromium 失败后回退 WeasyPrint，可设置为 `auto`。

## 关键设计

- 所有接口以 `/api` 开头，统一响应 `{ code, message, data }`。
- MySQL 表不使用物理外键，简历主体和模板配置使用 JSON 字段。
- 注册验证码、发送限频和并发注册锁存储在 Redis，并通过 TTL 自动过期。
- `preview_service` 用 Jinja2 渲染 HTML，预览和 PDF 导出共用该服务。
- 富文本与 AI 输出进入 HTML 前由 `rich_text_service` 用 `bleach` 白名单清理；Markdown 渲染在前端（`src/lib/markdown.ts`，markdown-it）完成，后端不做 Markdown 转换。
- AI 能力不在后端实现：后端只做鉴权、上下文装配、计费与落库，实际推理由 `ai-service`（pi-agent 运行时）承担，通过 `AI_SERVICE_URL` + `AI_SERVICE_TOKEN` 调用。
- 简历翻译通过独立 AI 任务执行，保持模块结构、事实字段和富文本格式，并由 Flow Points 规则单独计费。
- 头像存储通过统一存储服务封装，`STORAGE_PROVIDER=minio` 使用 MinIO，`STORAGE_PROVIDER=aliyun_oss` 使用阿里云 OSS。

## 对象存储

默认使用 MinIO：

```env
STORAGE_PROVIDER=minio
STORAGE_PUBLIC_URL_MODE=proxy
```

切换到阿里云 OSS：

```env
STORAGE_PROVIDER=aliyun_oss
ALIYUN_OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
ALIYUN_OSS_ACCESS_KEY_ID=你的 AccessKey ID
ALIYUN_OSS_ACCESS_KEY_SECRET=你的 AccessKey Secret
ALIYUN_OSS_BUCKET=vitaflow
ALIYUN_OSS_PUBLIC_URL=https://vitaflow.oss-cn-hangzhou.aliyuncs.com
```

`STORAGE_PUBLIC_URL_MODE=proxy` 时上传后返回 `/api/files/{object_name}`，由后端代理读取文件，适合私有 bucket。`public` 模式会直接返回对象存储或 CDN 的公开地址。
