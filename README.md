# VitaFlow 简历流

VitaFlow 是一个面向求职者的智能简历生成与优化平台，提供在线简历编辑、AI 生成与诊断、JD 匹配优化、多模板预览、分享，以及 PDF/Word 导出能力。项目采用前后端分离架构，后端基于 FastAPI，前端基于 Vue 3 + Vite。

**线上体验：[https://vitaflow.vip](https://vitaflow.vip)**

## 主要功能

- 账号注册、邮箱验证码、JWT 登录与个人资料管理
- 简历列表、创建、复制、版本保存、文件导入与公开分享
- 三栏式简历编辑器，支持模块化编辑、拖拽排序、头像裁剪上传和自动保存
- 多套简历模板，支持字体、颜色、间距、头像显示等样式配置
- 后端 HTML 预览，浏览器预览与 PDF 导出共用渲染服务
- AI 简历生成、简历评分、模块润色、JD 匹配优化和简历对话
- AI 简历中英互译，支持语言智能识别、结构保护、翻译记录和独立 Flow Points 计费
- Flow Points 点数、AI 使用记录、兑换码与点数流水
- PDF/Word 导出，PDF 默认使用 Playwright + Chromium，支持 WeasyPrint 兜底
- MinIO / 阿里云 OSS 对象存储，支持代理访问或公开 URL
- 用户反馈、站内公告、模板管理和管理后台

## 技术栈

- 后端：FastAPI、SQLAlchemy、Alembic、MySQL、Redis、LangChain、LangGraph
- 前端：Vue 3、TypeScript、Vite、Vue Router、Pinia、Tailwind CSS
- 导出：Playwright、Chromium、WeasyPrint、python-docx
- 存储：MinIO、阿里云 OSS
- AI 服务：Node.js 旁路服务，承载 pi-agent 运行时（`ai-service/`，见 `docs/pi-agent重构设计.md`）

## 项目结构

```text
VitaFlow/
├── backend/                         # FastAPI 后端
│   ├── app/
│   │   ├── api/                     # 认证、简历、AI、分享、导出及管理端接口
│   │   ├── core/                    # 配置、数据库、Redis、安全、日志和统一响应
│   │   ├── models/                  # SQLAlchemy 数据模型
│   │   ├── schemas/                 # Pydantic 请求与响应结构
│   │   ├── services/                # 核心业务服务
│   │   │   ├── ai/                  # LLM、Prompt、Chain、Graph 与 Token 统计
│   │   │   └── storage/             # MinIO / 阿里云 OSS 统一存储层
│   │   ├── static/                  # 字体及简历预览、打印公共样式
│   │   ├── templates/resume/        # Jinja2 简历模板与模板片段
│   │   └── main.py                  # FastAPI 应用入口
│   ├── alembic/
│   │   └── versions/                # 数据库迁移版本
│   ├── .env.example                 # 后端环境变量示例
│   ├── alembic.ini                  # Alembic 配置
│   ├── requirements.txt             # Python 依赖
│   └── README.md                    # 后端开发与部署说明
├── ai-service/                      # Node AI 服务（pi-agent 旁路，骨架只读对话）
│   ├── src/                         # server / agent / faux / prompt / config
│   └── README.md                    # AI 服务说明与运行方式
├── frontend/                        # Vue 3 前端
│   ├── src/
│   │   ├── api/                     # Axios 封装及各业务 API
│   │   ├── components/
│   │   │   ├── admin/               # 管理后台组件
│   │   │   ├── ai/                  # AI 生成、优化、评分、翻译与对话组件
│   │   │   ├── editor/              # 简历编辑器及字段表单
│   │   │   ├── preview/             # A4、HTML 与 PDF 预览组件
│   │   │   ├── resume/              # 简历列表、创建、导入与分享组件
│   │   │   ├── templates/           # 前端模板预览组件
│   │   │   └── ui/                  # 通用基础 UI 组件
│   │   ├── router/                  # 路由定义与访问守卫
│   │   ├── stores/                  # 用户、简历和编辑器 Pinia 状态
│   │   ├── styles/                  # 全局样式
│   │   ├── types/                   # API、用户、简历和 AI 类型定义
│   │   ├── utils/                   # Diff、分页、打印和本地化工具
│   │   ├── views/                   # 页面级视图
│   │   ├── App.vue                  # 根组件
│   │   └── main.ts                  # 前端应用入口
│   ├── .env.example                 # 前端环境变量示例
│   ├── package.json                 # 前端依赖与脚本
│   ├── tailwind.config.js           # Tailwind CSS 配置
│   ├── vite.config.ts               # Vite 配置
│   └── README.md                    # 前端开发说明
├── AGENTS.md                        # 代码代理与协作约束
├── LICENSE                          # PolyForm Noncommercial 许可证
└── README.md                        # 项目总览
```

## 环境要求

- Python 3.9+
- Node.js 18+
- MySQL 8+
- Redis 6+
- MinIO 或阿里云 OSS，头像、公告图片、AI 对话图片等上传能力需要对象存储
- Chromium 或 Playwright 浏览器，PDF 导出需要

## 快速开始

### 0. 一键 Docker 部署（全栈，可选）

需要 Docker Desktop / Docker Engine，在仓库根目录执行：

```bash
docker compose --profile full up -d --build
```

首次构建会下载 Chromium、中文字体与 OCR 依赖，约 10–20 分钟。启动后：

- 前端：<http://localhost:8080>
- 后端接口文档：<http://localhost:8000/docs>
- MinIO 控制台：<http://localhost:9001>

默认的 `docker compose up -d` 只启动 MySQL / Redis / MinIO / AI 服务，前后端继续按下面的步骤在宿主机运行，适合日常开发（保留热更新）。两种模式的完整说明见 [docs/本地开发环境启动指南.md](docs/本地开发环境启动指南.md)。

> 构建默认走阿里云 Debian / PyPI 镜像；海外部署可在根目录 `.env` 设置 `APT_MIRROR` 与 `PIP_INDEX_URL` 为官方源。

### 1. 创建数据库

```sql
CREATE DATABASE vitaflow DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 2. 启动后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

根据本地环境修改 `backend/.env`。至少需要配置 MySQL、Redis 和 `JWT_SECRET_KEY`；使用 AI、邮箱验证码、对象存储或导出时，还需要填写对应服务配置。

完成配置后执行数据库迁移并启动服务：

```bash
alembic upgrade head
python -m playwright install chromium
uvicorn app.main:app --reload
```

后端默认运行在 `http://127.0.0.1:8000`，所有业务接口默认以 `/api` 开头。启动后可访问 `http://127.0.0.1:8000/docs` 查看 OpenAPI 接口文档。

如果是 Linux 服务器，建议参考 [backend/README.md](backend/README.md) 安装 WeasyPrint 兜底依赖和中文字体。生产环境也可以配置 `PDF_CHROMIUM_EXECUTABLE_PATH` 指向系统 Chromium。

### 3. 启动前端

```bash
cd frontend
npm install
cp .env.example .env
```

确认 `frontend/.env` 中的 `VITE_API_BASE_URL` 指向已启动的后端 API，然后运行：

```bash
npm run dev
```

前端默认运行在 `http://localhost:5173`，示例配置中的后端地址为 `http://127.0.0.1:8000/api`。

## 常用命令

### 后端

```bash
cd backend
source .venv/bin/activate
alembic upgrade head
uvicorn app.main:app --reload
```

### 前端

```bash
cd frontend
npm run dev
npm run build
npm run preview
```

前端构建产物位于 `frontend/dist`。生产环境可由 Nginx 托管静态文件，并将 `/api` 反向代理到 FastAPI。

## 配置说明

完整配置示例见：

- [backend/.env.example](backend/.env.example)
- [frontend/.env.example](frontend/.env.example)

常用配置：

| 配置 | 用途 |
| --- | --- |
| `APP_ENV`、`APP_DEBUG` | 应用环境与调试模式 |
| `CORS_ORIGINS` | 允许访问后端的前端地址 |
| `DB_*` | MySQL 连接 |
| `REDIS_URL`、`REDIS_KEY_PREFIX` | Redis 连接、验证码、限频、缓存和并发锁 |
| `JWT_SECRET_KEY`、`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | 登录令牌签名与过期时间 |
| `ADMIN_EMAILS` | 管理员邮箱白名单，多个邮箱用英文逗号分隔 |
| `SMTP_*` | 邮箱验证码和反馈回复邮件 |
| `AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL` | OpenAI 兼容模型服务 |
| `STORAGE_PROVIDER` | `minio` 或 `aliyun_oss` |
| `STORAGE_PUBLIC_URL_MODE` | `proxy` 或 `public` |
| `EXPORT_DIR`、`UPLOAD_DIR` | 导出文件和上传文件目录 |
| `PDF_BASE_URL` | PDF 渲染时访问后端静态资源的基础地址 |
| `PDF_RENDERER` | `chromium`、`auto` 或 `weasyprint` |
| `PDF_CHROMIUM_EXECUTABLE_PATH` | 系统 Chromium 可执行文件路径，可留空使用 Playwright 自带浏览器 |
| `VITE_API_BASE_URL` | 前端访问的 API 地址 |

生产环境请关闭 `APP_DEBUG`，使用强随机 `JWT_SECRET_KEY`，并通过环境变量或密钥管理服务注入所有凭据。不要提交 `.env`、用户简历、导出文件、上传文件或日志。

## 对象存储

默认配置使用 MinIO：

```env
STORAGE_PROVIDER=minio
STORAGE_PUBLIC_URL_MODE=proxy
MINIO_ENDPOINT=127.0.0.1:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=vitaflow
```

切换到阿里云 OSS：

```env
STORAGE_PROVIDER=aliyun_oss
ALIYUN_OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
ALIYUN_OSS_ACCESS_KEY_ID=your-access-key-id
ALIYUN_OSS_ACCESS_KEY_SECRET=your-access-key-secret
ALIYUN_OSS_BUCKET=vitaflow
ALIYUN_OSS_PUBLIC_URL=https://vitaflow.oss-cn-hangzhou.aliyuncs.com
```

`STORAGE_PUBLIC_URL_MODE=proxy` 时，上传后返回 `/api/files/{object_name}`，由后端代理读取对象，适合私有 bucket。`public` 模式会直接返回对象存储或 CDN 的公开地址。

## PDF / Word 导出

PDF 导出默认使用 Chromium：

```env
PDF_RENDERER=chromium
PDF_BASE_URL=http://127.0.0.1:8000
```

可选模式：

- `chromium`：严格使用 Chromium，失败时直接报错，便于发现浏览器安装或路径问题
- `auto`：优先 Chromium，失败后回退 WeasyPrint
- `weasyprint`：仅使用 WeasyPrint

Word 导出由 `python-docx` 生成。PDF 和 Word 导出记录会写入后端导出记录表，并可在管理后台查看。

## 管理后台

在 `backend/.env` 中配置管理员邮箱：

```env
ADMIN_EMAILS=admin@example.com
```

多个管理员邮箱使用英文逗号分隔。执行 `alembic upgrade head` 后，白名单用户下次登录会同步为管理员，并可通过前端 `/admin` 进入管理后台。

管理后台包含用户、简历、AI 任务、Flow Points、兑换码、公告、反馈、导出记录、模板和系统设置等模块。

## API 与前端路由

后端主要 API 模块：

- `/api/auth`：注册、登录、验证码、用户资料
- `/api/resumes`：简历 CRUD、版本、预览、分享、导出
- `/api/templates`：模板列表和模板详情
- `/api/ai`：AI 生成、评分、润色、JD 优化、对话和点数
- `/api/files`：头像、公告图片、AI 对话图片和文件代理
- `/api/announcements`：站内公告
- `/api/feedback`：用户反馈
- `/api/admin`：管理后台

前端主要页面：

- `/`：首页（已登录自动进入工作区）
- `/landing`：营销落地页
- `/login`、`/register`：登录与注册
- `/workspace`：工作区首页（左侧栏为简历与会话入口，中间为对话）
- `/workspace/:id`：工作区内的简历编辑器与预览
- `/workspace/templates`：工作区内的模板中心（选好模板直接起稿）
- `/workspace/resumes`：工作区内的全部简历（管理、复制、删除、分页）
- `/resumes`：旧链接，重定向到 `/workspace/resumes`（保留 `?create=` 参数）
- `/resumes/:id/edit`：旧编辑链接，重定向到 `/workspace/:id`
- `/workspace/help`：工作区内的帮助中心
- `/workspace/profile`：工作区内的个人中心（`?tab=feedback` 直达反馈）
- `/workspace/announcements`：工作区内的公告历史
- `/workspace/ai-records`：工作区内的 Flow Points 与调用记录
- `/templates`：未登录访客的公开模板页（登录后重定向到 `/workspace/templates`）
- `/share/:token`：公开分享页
- `/help`、`/profile`、`/announcements`、`/ai-records`：旧链接，重定向到对应 `/workspace/*`
- `/workspace/admin`：管理后台，同样待在工作区外壳里；菜单按宽度自适应（窄屏横排 tab，≥1536px 才用左侧竖排）
- `/admin`：旧链接，重定向到 `/workspace/admin`

## 项目说明

受个人能力与实践经验所限，项目中难免存在考虑不周或实现不完善之处，欢迎反馈、指正与交流。

## 许可证

本项目采用 [PolyForm Noncommercial License 1.0.0](LICENSE)：

- 允许个人学习、研究、修改和非商业分发。
- 未经授权，不得将本项目用于商业目的。
- 如需商业使用，请联系项目维护者获取商业许可。
