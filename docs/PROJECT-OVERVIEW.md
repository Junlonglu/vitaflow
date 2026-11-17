# ResumeFlow 项目架构与功能说明

> 本文档用于向其他 AI / 协作者完整交代 ResumeFlow 的架构、模块边界、领域模型与运行约束。
> 读完本文应能独立定位代码、判断改动影响面，而不需要先通读仓库。
> 文末附「术语表」与「已知技术债」，避免误读和重复踩坑。

---

## 1. 项目定位

ResumeFlow（中文名「简历流」）是一个面向求职者的**智能简历平台**：在线编辑简历 → AI 对话式优化 → 多模板预览 → 导出 PDF / JSON → 生成分享链接。

核心特征：

- **结构化简历**：简历不是富文本，而是一份受 schema 约束的 JSON（`resume_data`），前端按 section 渲染表单，后端按 section 渲染模板。
- **AI 不直接改数据**：AI 只能产出「待确认变更（pending change）」，用户点「应用」后才写库。信任边界在 Python 侧。
- **预览 / PDF / 分享三处同源**：共用后端 Jinja2 HTML 渲染，模板只有一份实现。
- **完整运营闭环**：注册登录、积分（flow points）计费、兑换码、公告、反馈、管理后台、导出记录。

---

## 2. 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite + Vue Router + Pinia + Tailwind CSS；axios 拦截器统一请求；EventSource 不可用，AI 流式走 `fetch` + `ReadableStream` |
| 后端 | FastAPI + SQLAlchemy 2.x + Alembic + Pydantic v2；MySQL 8.0 + Redis 7 + MinIO/阿里云 OSS；Jinja2 模板；Playwright(Chromium) 出 PDF；python-docx 出 Word |
| AI 服务 | Node 22 + Express 4 + `@earendil-works/pi-coding-agent@0.83.0`（锁定版本，含 `@earendil-works/pi-ai`）；tsx 运行 TS |
| 部署 | Docker Compose；MySQL / Redis / MinIO / ai-service 常驻，backend 与 frontend 可选容器化（`--profile full`） |

> AI 管线已迁至 `ai-service/`（pi-agent 运行时）：`requirements.txt` 中已无 langchain 依赖，旧链路退役后 `backend/app/services/ai/` 仅剩 `token_usage.py`。

---

## 3. 运行时拓扑

```
浏览器 (Vue SPA)
   │  ① /api/*  → axios，统一 {code,message,data}
   │  ② /api/ai/**/stream → fetch + ReadableStream 读 NDJSON
   ▼
FastAPI :8000  ──── 唯一事实源、唯一鉴权点、唯一计费点
   │  ③ POST /v1/chat、/v1/task      （Bearer AI_SERVICE_TOKEN）
   ▼
Node ai-service :8300  ── pi-agent 运行时，无数据库权限
   │  ④ 反向回调 POST /api/ai/service/tool/*、/api/ai/service/usage
   └──────────────► 回到 FastAPI
```

依赖中间件：MySQL 3306（事实源）、Redis 6379（验证码/限频/公告缓存）、MinIO 9000（头像、公告图、AI 对话图、导出文件）。

三种端口布局：开发态 `5173 / 8000 / 8300`；全栈态 `8080(Nginx) / 8000 / 8300`，Nginx 把 `/api` 反代到 backend。

**关键设计约束**：前端**不直连** ai-service；ai-service **不连数据库**；平台模型 API Key 只存在于 ai-service 进程。用户自带（BYOK）密钥是唯一例外：由 FastAPI 用**独立于 JWT 的** Fernet 口令加密存入 MySQL（`fernet:<kid>:<token>`，支持多版本轮换），明文只在「服务间 /v1/chat 请求」与 ai-service 当次进程内存中出现，浏览器永远只拿到掩码；所有日志与错误回显统一脱敏。

---

## 4. 仓库结构

```
vitaflow/
├── AGENTS.md              # 仓库级开发约束（代码代理必读，但 AI 技术栈段落已过时）
├── CONTEXT.md             # 领域术语表
├── README.md              # 总入口
├── docker-compose.yml
├── backend/               # FastAPI，约 13k 行 Python
│   ├── app/
│   │   ├── api/           # 13 个路由模块，只做参数解析与编排
│   │   ├── services/      # 24 个业务服务
│   │   ├── models/        # 10 个 SQLAlchemy 模块
│   │   ├── schemas/       # 11 个 Pydantic 模块
│   │   ├── core/          # config / security / response / exceptions / database / resume_schema（简历 JSON 契约）
│   │   ├── templates/resume/  # 12 套 Jinja2 简历模板
│   │   └── main.py
│   ├── alembic/versions/  # 39 个迁移
│   └── tests/             # 25 个测试文件
├── frontend/              # Vue 3，约 17k 行
│   └── src/{views,components,stores,api,router,types,utils}/
├── ai-service/            # Node 旁路服务
│   ├── src/{server,agent,prompt,skills,taskPrompts,catalog,billing,usage,config,real,faux}.ts
│   ├── src/tools/         # 工具定义（resume / library / skill / questions / search）+ state + http 客户端 + 步骤文案
│   └── .pi-agent/models.json   # 模型目录（能力清单 + API Key 引用）
└── docs/
    ├── README.md        # 文档索引（唯一入口）
    ├── PROJECT-OVERVIEW.md        # 架构与功能总览
    ├── design-system.md           # 设计规范
    ├── 本地开发环境启动指南.md     # 启动与排障
    ├── ai/              # AI 专题：resume-agent / ai-call-flows / ai-chat-pending-change / ai-trajectory
    ├── architecture/    # pi-agent重构设计（含旧链路回滚依据）
    ├── data/            # resume-schema：简历 JSON 契约与原子信息模型
    ├── learning/        # 面试与复盘笔记（非现状依据）
    └── archive/         # 已归档的一次性文档
```

---

## 5. 后端架构

### 5.1 分层约定

| 目录 | 职责 | 禁止 |
|---|---|---|
| `api/` | 参数校验、鉴权依赖、调用 service、包装响应 | 堆业务逻辑 |
| `services/` | 业务规则、事务边界、跨模块编排 | 直接返回 ORM 对象给前端 |
| `models/` | SQLAlchemy 实体 | 写业务逻辑 |
| `schemas/` | Pydantic 请求/响应契约 | 引用 ORM 内部字段名以外的实现细节 |
| `core/` | 配置、鉴权、统一响应、异常、DB 会话 | 依赖具体业务 |

统一响应：`{ "code": 0, "message": "...", "data": ... }`（`core/response.py`）。
异常：`core/exceptions.py` 把内部异常转成中文友好提示，不泄露堆栈。

### 5.2 API 路由总表（全部挂 `/api`）

**认证 `auth.py`**
`GET /user-agreement`、`POST /verification-code`、`POST /password-reset-code`、`POST /register`、`POST /login`、`POST /reset-password`、`GET /me`、`PUT /profile`、`PUT /password`

**简历 `resumes.py`**
`GET /resumes`（支持 `page` / `page_size` / `keyword` 标题模糊搜索，LIKE 通配符转义 / `sort=updated|created|title`，非法值回落默认）、`POST /resumes`、`POST /resumes/from-starter`、`POST /resumes/import-file`（上传 docx/pdf/图片/txt/md 走 AI 结构化；上传导出过的 `.json` 直接回填，不调模型、不扣积分）、`GET|PUT|DELETE /resumes/{id}`、`POST /resumes/{id}/duplicate`、`POST /resumes/{id}/versions`（存版本快照）、`GET|PUT /resumes/{id}/share`（开关与 token）、`GET|POST /resumes/{id}/preview-html`（HTML 预览，POST 支持传入未保存的草稿）、`GET /resumes/{id}/preview-pdf`

**导出 `exports.py`**
`POST /resumes/{id}/export/pdf`、`POST /resumes/{id}/export/json`（结构化快照，流式返回不落盘）、`POST /resumes/{id}/export/word`（已下线，返回 410）

**AI `ai.py`（最核心，约 900 行）**
- 能力与记录：`GET /ai/capability`、`GET /ai/records`、`GET /ai/histories`
- 积分：`GET /ai/flow-points`、`GET /ai/flow-points/transactions`、`POST /ai/flow-points/redeem`
- 流式：`POST /ai/generate-resume/stream`（按目标岗位生成整份简历）、`POST /ai/resume-chat/{resume_id}/messages/stream`、`POST /ai/resume-chat/{resume_id}/messages/{message_id}/regenerate/stream`
- 会话（一份简历多条对话）：`GET /ai/resume-chat/{resume_id}/sessions`（置顶优先 + 含条数与末条预览）、`POST .../sessions`、`PATCH .../sessions/{session_id}`（重命名）、`POST .../sessions/{session_id}/pin`（置顶/取消）、`DELETE .../sessions/{session_id}`
- 会话搜索（跨简历，命中标题或任意消息内容）：`GET /ai/resume-chat/sessions/search?q=&limit=`，结果带 `resume_title`
- 变更决策：`POST /ai/resume-chat/{resume_id}/messages/{message_id}/decision`（apply / reject）、`DELETE /ai/resume-chat/{resume_id}/messages?session_id=`
- 历史：`GET /ai/resume-chat/{resume_id}/messages?session_id=`（省略 session_id 时落在该简历最近一次会话，兼容旧前端）
- **服务间回调（供 ai-service 调用）**：`POST /ai/service/tool/update-basics-field`、`POST /ai/service/tool/update-section`、`POST /ai/service/tool/propose-change`、`GET /ai/service/tool/get-resume-context`、`POST /ai/service/tool/search-evidence`、`GET /ai/service/tool/info-fragments/search`（按关键词/分类检索用户信息库）、`POST /ai/service/tool/info-fragments/import`（生成待确认的简历修改，不落库）、`POST /ai/service/tool/info-fragments/propose`（生成待确认的信息条目，不落库）、`POST /ai/service/usage`

**信息库（原子信息）`info_fragments.py`**
- 列表：`GET /info-fragments`（`kind` / `q` 关键词 / `tag` / `page` / `page_size`，返回 `items` + `total` + 各分类 `counts`）、`GET /info-fragments/meta`（分类定义、字段表、上限）
- 写入：`POST /info-fragments`（指纹重复时合并）、`POST /info-fragments/bulk`（单次 30 条）、`PUT|DELETE /info-fragments/{id}`
- 流转：`POST /info-fragments/extract`（从指定简历提取，已存在跳过）、`POST /info-fragments/apply`（把若干条导入简历，返回最新 `resume_data` 与新增条目名，重复导入返回 400）

**头像库 `avatars.py`**
- 不单独建表：复用 `uploaded_files` 的 `file_type="avatar"` 记录当库，上传即入库（`POST /files/upload-avatar`），账号级，每人最多 30 张。
- 列表：`GET /avatars`（返回 `items` + `total` + `limit`，每张带 `usage_count`＝有多少份简历正在用它）
- 移除：`DELETE /avatars/{id}`（只清索引，不删存储对象——已写入简历的图片仍然可访问，避免已分享简历裂图）
- 简历里 `basics.avatar` 只存对象名（`avatar/{user_id}_{uuid}.jpg`），渲染时拼成代理 URL；旧数据里的完整 URL 继续兼容。头像**不随 JSON 导出**：导出时剥掉本系统引用，导入时残留引用会被清空并在成功提示里说明。

**用户自带模型与技能 `user_ai.py`**（Ticket 17 / 18）
- **多模型配置**：`GET|POST /user-ai/models`、`PUT|DELETE /user-ai/models/{id}`、`POST /user-ai/models/{id}/default`（设为默认）、`POST /user-ai/models/{id}/test` 与 `POST /user-ai/models/test`（未保存配置探活）、`POST /user-ai/models/remote-models`（按地址与密钥拉上游 `/models`，带 models.dev 资料，不落盘；页面用它做「选模型」而不是「手填模型名」）。每用户最多 10 条，任意时刻恰好一条默认项；删除/停用默认项会自动补位。对话请求可带 `user_model_id`（正数指定某条自带模型、`0` 明确使用平台模型、省略则按服务端默认解析），在编辑器模型选择器里与平台模型同列展示。
- **推理强度**：档位是上游参数原词 `auto/off/minimal/low/medium/high/xhigh/max`（`auto` 即界面的 `default`）。用户端每条 BYOK 配置存 `reasoning_effort`，非 auto 档透传到 ai-service，注册模型时置 `reasoning=true` + `compat.supportsReasoningEffort`，把 thinkingLevel 交给 `createAgentSession`；平台模型则在 `models.json` 里存同名字段（后台「模型资料 · 推理强度」），ai-service 每轮按 model ref 读它当 thinkingLevel，没有就保持默认（推理模型 medium、其它 off）。两条路最终都映射为 OpenAI 兼容端点的 `reasoning_effort`。
- **技能**：`GET|POST /user-ai/skills`、`PATCH|DELETE /user-ai/skills/{id}`（含启用开关）、`POST /user-ai/skills/import`（单个 .md/.txt/.zip）、`POST /user-ai/skills/import-package`（ZIP 或 `webkitdirectory` 文件夹，多文件按 `SKILL.md` 分组，附属文本拼为「参考资料」）。
- **密钥与隐私**：`GET /user-ai/security`（部署加密状态与存储说明）、`GET /user-ai/audit-logs?limit=20`（当前用户使用记录，上限 50）、`DELETE /user-ai/keys`（删除全部模型配置与密钥，不可恢复）。CRUD、设为默认、测试连接、对话使用、密文轮换都会写审计。
- 旧接口 `GET|PUT|DELETE /user-ai/credential`、`POST /user-ai/credential/test` 保留兼容，语义落在「默认配置」上。读接口只回掩码；所有查询带 `user_id`，跨用户访问返回 404。

**其余**
- `shares.py`：`GET /shares/{token}`（公开分享，无需登录）
- `templates.py`：`GET /templates`、`GET /templates/{id}`
- `files.py`：`POST /files/upload-avatar`、`/upload-announcement-image`、`/upload-ai-chat-image`、`GET /files/font-assets/{name}`、`GET /files/{object_name}`（存储代理）
- `feedback.py`：`POST /feedback`、`GET /feedback`
- `announcements.py`：`GET /announcements`、`GET /announcements/current`、`GET /announcements/{id}`、`POST /announcements/{id}/dismiss`
- `resume_starters.py`：`GET /resume-starters`（按行业预置的简历起手式）
- `admin.py`（约 2157 行，管理员专用）：公告 CRUD、仪表盘、站点设置、反馈处理与回信、积分批量发放/撤销、积分流水、用户管理、简历总览、AI 任务、AI 模型定价（`/ai-configs`、`/model-pricing`）、**模型目录代理**（`/ai-catalog*`，转发到 ai-service）、积分规则、兑换码（含导入/导出）、导出记录、模板管理、起手式与行业模板管理

### 5.3 服务层职责

| 服务 | 作用 |
|---|---|
| `ai_service_client.py` | 到 ai-service 的 HTTP 客户端：`/v1/chat` 与 `/v1/task`，解析 NDJSON 流并转成 SSE/回调；统一注入 `AI_SERVICE_TOKEN` |
| `ai_chat_service.py` | 1218 行，AI 对话总控：会话/消息持久化、上下文构造、模型选择、工具结果校验、增量合并、变更决策、后台滚动摘要 |
| `ai_task_service.py` | 一次性任务（生成简历、导入解析、摘要压缩）的调用与结果落库 |
| `ai_config_service.py` | `ai_model_configs` 业务策略 CRUD，与 ai-service 模型目录做 `model_ref` 关联 |
| `flow_points_service.py` | 积分账户、扣减、发放、流水、兑换码核销 |
| `preview_service.py` | Jinja2 渲染简历 HTML，12 套模板共用，注入 `template_config`、字体、图标 |
| `pdf_export_service.py` | Chromium(Playwright) 渲染 PDF，带缓存指纹（URL + 模板 + 数据 hash） |
| `word_export_service.py` | python-docx 生成 Word，需与 HTML 模板保持内容一致 |
| `resume_import_service.py` | 上传 docx/pdf → 抽取文本 → 交给 AI 解析成结构化简历 |
| `resume_normalization.py` / `resume_locale.py` | 简历 JSON 规范化（补默认字段、修类型）与多语言字段处理 |
| `info_fragment_service.py` | 信息库（原子信息）：增删改查、指纹去重、从简历提取、合并导入简历、Agent 检索与提案校验 |
| `core/resume_schema.py` | 简历 JSON 的声明式契约：字段表、schema 版本、`normalize_resume_document`、Agent 写入契约、原子信息分类 |
| `rich_text_service.py` | 富文本清理，AI 输出进入 HTML 前必经 |
| `storage/` | 统一存储抽象：MinIO / 阿里云 OSS / 本地，三种实现 |
| `search_evidence_service.py` | 支撑 `web_search` 工具的证据落库与引用 |
| `announcement_cache_service.py` | 公告 Redis 缓存，带前缀与 TTL |

### 5.4 数据模型

**业务表**

| 表 | 说明 |
|---|---|
| `users` | 账号、邮箱、密码哈希、积分余额、状态、是否管理员 |
| `resumes` | `title`、`template_id`、`template_config`(JSON)、`resume_data`(JSON)、`share_token`、是否公开 |
| `resume_versions` | 简历快照（手动存档） |
| `resume_templates` | 模板元数据（名称、分类、封面、启用状态） |
| `resume_starters` / `resume_starter_industry_templates` | 行业起手式与行业→模板绑定 |
| `export_records` | 导出流水（格式、文件、耗时、状态） |
| `uploaded_files` | 上传文件元数据（对象名、大小、MIME、归属） |
| `announcements` / `announcement_reads` | 公告与已读状态 |
| `user_feedbacks` | 用户反馈与处理状态、回信记录 |

**AI 相关表**

| 表 | 说明 |
|---|---|
| `ai_chat_sessions` | 会话（按 user + resume 组织，一份简历可有多条对话），`is_pinned` 置顶标记，含滚动摘要 `context_summary` 与其覆盖到的 `summary_message_id` |
| `ai_chat_messages` | 消息；assistant 消息可带 `optimized_resume_data`(待确认的简历变更)、`pending_info_fragments`(待确认入库的信息条目)、`suggestions`(变更摘要)、`action_status`(pending/applied/rejected)、usage 字段 |
| `info_fragments` | **信息库（原子信息）**：用户级的一条事实。`kind`(15 类，决定落到简历哪个模块)、`title`、`summary`、`payload`(JSON，复用简历条目字段)、`tags`(JSON)、`search_text`(扁平检索列)、`fingerprint`(sha1 去重)、`source`(manual/resume/ai)、`enabled`、`usage_count`、`last_used_time`；每用户上限 500 条 |
| `ai_tasks` | 一次性任务记录（类型、输入、输出、状态、错误、耗时、token） |
| `ai_model_configs` | **业务策略**：`model_ref` 指向模型目录中的 provider/model；含 `name`、`context_messages`、`is_chat_selectable`、`sort_order`、`is_active`，以及三种费率 `chat_points_per_call` / `chat_points_per_million_input_tokens` / `chat_points_per_million_output_tokens` |
| `flow_point_rules` | 各功能（生成/对话/导出…）的积分单价 |
| `flow_point_transactions` | 积分流水（增减、原因、关联对象） |
| `flow_point_redeem_codes` / `flow_point_redeem_records` | 兑换码与核销记录 |
| `app_settings` | 站点级开关（注册开关、公告、AI 默认模型等） |
| `user_ai_credentials` | **BYOK 多配置**：每用户最多 10 条，`(user_id)` 不再唯一，改由 `is_default` 标出默认项；`api_key_encrypted`（`fernet:<kid>:<token>`，密钥由独立 `BYOK_ENCRYPTION_KEYS` 派生）+ `api_key_masked`、`base_url`、`model`、`reasoning_effort`、`enabled`、`supports_image`、上下文/输出上限；明文永不落库、永不回前端 |
| `user_skills` | 用户手写 / 单文件 / 技能包导入的技能：`slug`（同用户内唯一）、`name`、`description`、`instructions`、`enabled`、`source`（`manual`/`upload`/`package`）；启用中的才拼进系统提示词 |
| `user_ai_audit_logs` | BYOK 密钥变更与使用审计：`user_id`、`action`、`credential_id`（删除后仍留作历史引用）、`label`、脱敏后的 `detail`、`create_time`；**不存明文、掩码或密文**，按用户隔离，最多回最近 50 条 |

> 约定：**MySQL 表不使用物理外键**，关联靠业务字段 + 应用层校验。所有结构变更必须新增 Alembic 迁移，不改旧迁移。

---

## 6. 领域模型：简历 JSON

这是全项目最重要的数据结构，前端表单、AI 工具、渲染模板、导出服务都以它为契约。

**声明式契约**：字段表、类型、必填与长度上限全部定义在 `backend/app/core/resume_schema.py`（`SECTIONS` / `FRAGMENT_KINDS`），并由 `normalize_resume_document()` 在**所有落库路径**（前端保存、AI 写回、信息库导入）收口；`schema_compact()` 把同一份字段表压成「一行一模块」随上下文下发给 Agent（`schema_prompt()` 保留给文档与调试）。字段级说明见 [`data/resume-schema.md`](./data/resume-schema.md)，此处只列结构。

```jsonc
{
  "schema_version": "1.1",        // 字段表版本，写入时由后端补齐
  "basics": {
    "name": "", "title": "", "status": "", "phone": "", "email": "",
    "location": "", "expected_salary": "", "highest_degree": "",
    "website": "", "github": "", "avatar": "",
    "custom_fields": [],          // 自定义字段 { key, label, value, icon }
    "field_config": {             // 每个字段的展示配置（标签/图标/行/顺序）
      "phone": { "label": "电话", "icon": "Phone", "row": 1, "order": 1 }
    }
  },
  "summary": { "content": "" },
  "education": [{ "id": "edu_1", "school": "", "major": "", "degree": "",
                  "start_date": "2022.09", "end_date": "2026.06", "description": "" }],
  "skills":    [{ "id": "skill_1", "name": "", "keywords": [], "description": "" }],
  "work":      [{ "id": "work_1", "company": "", "position": "", "start_date": "",
                  "end_date": "", "description": "", "highlights": [] }],
  "projects":  [{ "id": "project_1", "name": "", "role": "", "start_date": "",
                  "end_date": "", "tech_stack": "", "description": "", "highlights": [] }],
  "awards":    [{ "id": "award_1", "name": "", "date": "", "description": "" }],
  "custom_sections": [{ "id": "custom_1", "title": "", "preset_type": "", "items": [] }],
  "layout": {
    "section_order": ["basics","summary","education","skills","work","projects","awards"],
    "hidden_sections": [],
    "skills_options": { "show_keywords": true, "description_inline": false },
    "field_labels": {},
    "section_titles": { "basics": "基本信息", "summary": "个人简介" }
  }
}
```

要点：
- 列表型 section 的每个条目都有稳定 `id`，AI 的增量更新**按 id 定位**，避免整段覆盖；缺 id / id 重复由规范化补成 `{prefix}_{n}`。
- 日期一律 `"YYYY.MM"`；`highlights` / `keywords` 是字符串数组（也接受换行/分号文本，规范化时切分去重）。
- `layout` 控制顺序、显隐、标题文案，模板只负责视觉，不决定结构；Agent 契约明确「不要手改 layout」。
- `custom_sections` 支持用户自定义模块（证书 / 语言 / 竞赛 / 培训 / 论文 / 志愿 / 实习），用 `preset_type` 标记类型，渲染、导入与信息库都按它匹配。
- 规范化只做「补齐 + 清洗」，**不删除未知字段**，旧简历的历史字段不会因升级被吞掉。

---

## 7. AI 子系统（本项目复杂度中心）

### 7.1 为什么有独立进程

pi-agent 是 TypeScript SDK，无法在 Python 进程内运行；把整个后端改写成 Node 也不划算（认证、ORM、导出、存储生态都在 Python）。因此采用 **sidecar 模式**：FastAPI 拥有全部业务与数据权限，Node 只做一个「无状态 Agent 执行器」。决策依据见 §14。

### 7.2 HTTP 契约

| 接口 | 用途 |
|---|---|
| `GET /health` | 健康检查 |
| `POST /v1/chat` | 对话回合，返回 **NDJSON 流** |
| `POST /v1/task` | 一次性任务，返回单次结果 JSON |
| `GET /v1/catalog`、`/v1/catalog/env-keys` | 读模型目录与环境 Key 是否存在 |
| `POST/PUT/DELETE /v1/catalog/providers[/:key][/models[/:modelId]]` | 模型目录 CRUD，写后 `rebuildRuntime()` 热生效 |

鉴权：`Authorization: Bearer <AI_SERVICE_TOKEN>`，未配置或错误一律 401（fail-closed）。浏览器不持有该 token。

### 7.3 会话策略：请求级、无状态

每个 `/v1/chat` 请求：

```
createAgentSession({ cwd, sessionManager: SessionManager.inMemory(cwd), resourceLoader, tools, systemPromptOverride })
  → 订阅事件并桥接成 NDJSON
  → 结束后 finally: unsubscribe() → abort(若未完成) → dispose()
```

- 不写本地 JSONL 会话，多用户共享同一进程也互不污染，可水平扩容。
- 代价：上下文必须每轮由 FastAPI 注入。`build_ai_service_context()` 下发：
  - `resume`：`id` / `title` / `data`（简历全文，模型直接看，不再需要「先读一遍」）
  - `resume_fields`：`schema_compact()` 的一行一模块字段清单
  - `context_summary`：滚动压缩摘要（超出窗口的旧消息）
  - `history`：最近 N 条消息（N 取模型定价行 `context_messages`，夹在 1..40）
  - `pending_change`：是否存在待确认变更及其摘要
- 长对话压缩：回合结束后 `asyncio.create_task` 起后台任务，调 `/v1/task` 的 `chat_summary` 增量合并旧消息进 `ai_chat_sessions.summary`。

### 7.4 事件协议（Node → NDJSON → FastAPI → 浏览器）

`start` → `phase`(阶段提示) → `delta`(正文增量) / `thinking`(思考增量) / `tool`(工具开始/结束/进度) → `result`(最终结果与 usage) → `error`。

- 只订阅 subscribe 可见的事件：`message_update`(text_delta/thinking_delta)、`message_end`(usage)、`tool_execution_start/end`、`agent_settled`。
- 结束信号用 `agent_settled`（不是 `agent_end`），并在 `finally` 里二次兜底 `sendResult`，避免压缩/重试流程未完成就判结束。
- 前端 `api/ai.ts` 用原生 `fetch` + `ReadableStream` 逐行解析，**绕开 axios**（axios 无法增量读流）；普通接口仍走 axios 拦截器。

### 7.5 系统提示词与技能

提示词分三层，越靠下注入得越晚、越窄（细节见 [`ai/resume-agent.md`](./ai/resume-agent.md)）：

| 层 | 内容 | 量级 |
|---|---|---|
| 常驻 | `DEFAULT_SYSTEM_PROMPT`（`src/prompt.ts`）：唯一写入入口 `update_resume`、事实底线、信息库用法、回复风格，外加一行技能目录 | 约 600 字 |
| 每轮 | `buildContextPrompt()`：字段清单（一行一模块）→ 用户写作偏好 → 本轮技能正文 → 当前简历 JSON（代码块）→ 摘要 → 最近对话 → 待确认修改 | 随简历与历史变化 |
| 按需 | 内置技能正文（`src/skills.ts`）、用户自建技能（`req.body.skills`） | 只在本轮用到时 |

**顺序不是随意的**：按「常驻规则 → 字段结构 → 写作偏好 → 技能正文 → 简历 → 摘要 → 历史 → 待确认」排，稳定内容连续在前，供供应商前缀缓存命中；顺序只在 `buildContextPrompt()` 一处决定，技能正文与写作偏好通过 `options` 传入而非预先拼进 `basePrompt`。

- **写入契约**：`schema_compact()` 把每个模块压成一行（`work: id, company, position, …`），简历全文随上下文下发，模型照现有 JSON 的形状写字段。字段表只有 Python 一份（`core/resume_schema.py`），Node 不复制、不推断字段名；合法性由唯一的工具端点判定。
- **内置技能（6 个）**：诊断 / JD匹配 / 重写经历 / 润色 / 翻译 / 开场白。前端点技能卡或输入 `/名称`，`extractSkillCommand()` 在服务端解析，用户消息被替换成技能自带的 `ask`，技能正文注入这一轮的系统提示词；用户用自然语言提需求时，模型按技能目录自行 `use_skill(name)` 取用。
- **为什么要技能而不是长提示词**：技能只写「做法」（步骤、判断标准、输出形状），不重复身份与事实底线；目录常驻、正文按需，避免每轮背着 6 份 SOP。
- **用户自建技能**（`user_skills`）追加在平台规则之后，章节名 `# 用户的写作偏好`，并声明「不能覆盖上方的平台规则」，防止用户文本伪造系统级章节。
- `prompt.ts` 仍显式覆盖 pi 的「编码助手」人设，关闭 `noContextFiles` / `noSkills`，并在传了 `resourceLoader` 后 `await loader.reload()`。
- `taskPrompts.ts`：三类一次性任务 `generate_resume` / `import_resume` / `chat_summary`，各自 system prompt + 约束（只输出合法 JSON、禁编造事实、禁编造量化指标）。

### 7.6 工具清单（白名单，共 5 个）

| 工具 | 作用 | 是否回调 FastAPI |
|---|---|---|
| `update_resume` | **唯一写入口**：一组 `changes[]`（`section` + `items` / `fields` / `removed_ids`）与 `from_library[]`，一次提交多模块改动或信息库导入 | 是 → `/service/tool/update-resume`；结果进 `pending_change` |
| `ask_user` | 向用户提问（缺信息时澄清，最多 3 个） | 否，仅产出结构化问题 |
| `search_library` | 检索用户信息库（关键词 + 分类，最多 20 条） | 是 → `/service/tool/info-fragments/search`（GET），只读 |
| `save_library` | 把对话里整理出的新事实存进信息库 | 是 → `/service/tool/info-fragments/propose`；结果进 `pending_fragments`，用户确认后才入库 |
| `use_skill` | 按名称取用内置技能正文 | 否，读 `src/skills.ts` |
| `web_search` | 联网搜索（公司/岗位/技术事实核查） | 是 → `/service/tool/search-evidence`；仅当配置 `SEARCH_PROVIDER` 时注册（可选第 6 个） |

**`read` / `bash` / `edit` / `write` 全部禁用**。多用户 Web 场景下 `bash` 是明确的安全风险，这一条不可回退。

为什么只有一个写工具：旧版四个入口（`update_basics_field` / `update_section` / `propose_change` / `import_info_fragments`）终点相同、形状不同，模型要在四者间选，经常选错或重复提交，前端步骤标签也跟着抖。现在 `basics` 只是一个普通模块，导入是 `from_library`，参数形状收敛为一组 change。

工具状态只累积、不落库：`src/tools/state.ts` 的 `TurnState` 三个字段 `pendingChange` / `pendingFragments` / `askedQuestions` 互相独立、可并存，由 `result` 事件一起回传；后端分别写 `optimized_resume_data` 与 `pending_info_fragments`。

### 7.7 四层防御：AI 为什么不直接改库

1. **工具不持数据库句柄**：Node 侧只做参数整理，增量 POST 给 Python。
2. **Python 侧加载真实简历**：不信任 Node 传来的现状，避免「模型基于过期快照改」。
3. **限定合并**：按 item id 合并，不允许新增未提供的条目 id，不允许越界改 section。
4. **数值防幻觉校验**：`_unverified_numeric_claims()` 比对原文，模型新增的百分比/数量/QPS 等量化结果会被标记或剔除。
5. **落成 pending**：结果写入 `ai_chat_messages.optimized_resume_data` + `action_status=pending`，用户点「应用」才执行合并写库；信息库条目的提案另存 `pending_info_fragments`，确认时先入库再写简历。

### 7.8 模型配置三分

| 内容 | 存放位置 | 理由 |
|---|---|---|
| 有哪些 provider/model、能力（reasoning/vision）、上下文窗口、API Key 引用 | `ai-service/.pi-agent/models.json` | 只有跑 Agent 的进程需要 |
| 启用哪个模型、展示名、费率、上下文条数、能力开关 | MySQL `ai_model_configs.model_ref` | 业务策略与计费口径归后端 |
| 默认模型、站点开关 | MySQL `app_settings` | 运营可改 |

Key 通过环境变量注入（如 `DEEPSEEK_API_KEY`），`models.json` 里只写 `$VAR` 引用，不落明文。

### 7.9 计费

- 计费单位是「积分（flow points）」，规则存 `flow_point_rules`，按功能类型定价。
- 对话：流结束后 ai-service 汇总 usage（按 provider/model 聚合，过滤 faux 模式）→ `POST /api/ai/service/usage` → Python 扣减并写流水。
- 一次性任务：FastAPI 侧同步结算。
- **BYOK 回合不消耗积分**：对话入口跳过 `precheck_flow_points`，`usage` 回调里 provider 以 `byok-` 开头时仍落一条 `ai_chat` 任务行（供成功率/功能统计），但 `finish_ai_task(settle=False)` 且 `points_used=0`。
- 两条路径语义不同（异步回调 vs 同步结算），新增任务类型时必须明确走哪一条。

### 7.10 用户自带模型（BYOK）与自定义技能

一条完整链路，跨越三层，任何一层失败都自动回落到平台默认：

```
设置页 /workspace/settings（多配置列表 + 内联编辑区 + 推理强度 + 技能包导入 + 密钥与隐私卡）
  → FastAPI /api/user-ai/*（Fernet 密文落库、只回掩码、SSRF 两级校验、日志脱敏、审计）
  → 对话时 _resolve_user_ai(db, user_id, user_model_id) 解析 override + 启用技能 + 独立 session 写使用审计
  → ai_service_client 在 /v1/chat payload 附带 model_override（含 reasoning_effort）/ skills
  → ai-service registerByokModel() 动态注册临时 provider（id = owner + 配置哈希 + 推理强度）
  → createAgentSession(thinkingLevel=...) → 兼容端点收到 reasoning_effort
  → buildContextPrompt(options.skills) 把技能偏好放在字段结构之后、简历之前
  → usage 回调按 provider 前缀识别 byok 并跳过结算
```

关键设计：

- **独立密钥与轮换**：`core/crypto.py` 用 `BYOK_ENCRYPTION_KEYS`（逗号分隔的 `kid=secret`）派生 Fernet 密钥，密文格式 `fernet:<kid>:<token>`；第一项用于写入，其余仅解密历史密文。旧版 `fernet:<token>`（`JWT_SECRET_KEY` 派生）仍可读，`GET /user-ai/models` 会把它透明重写为当前 kid，之后即可安全移除旧口令。未配置独立口令时回退 JWT 派生以兼容旧部署；但 `app_env=production/prod/staging` 且 JWT 仍是弱默认值时，**启动直接失败**（`main.py` lifespan 调 `validate_encryption_config(strict=True)`）。轮换登录密钥不再影响已存 API Key；解不开的密文按「未配置」处理，对话静默回退平台模型。改模型名/地址时 `api_key` 可留空沿用已存密钥。**密钥不在浏览器、不在 localStorage**：明文只随 HTTPS 请求到服务端，读回来永远是掩码。
- **日志与错误脱敏**：`core/redact.py` 的 filter 挂在所有 logging handler 上，先把 `msg % args` 合并成最终文本再脱敏（直接改模板会吃掉 `%s`），并预生成脱敏后的 `exc_text` 覆盖异常栈；`probe_credential` 对厂商错误体、httpx 异常与未预期异常一律 `redact()` 后才回前端。
- **可验证的控制权**：`user_ai_audit_logs` 记录谁在何时添加 / 修改 / 测试 / 使用 / 清除了哪条配置（不存任何密钥材料，配置删除后记录仍在）；`GET /user-ai/security` 返回真实部署状态（独立密钥 / 开发回退），`GET /user-ai/audit-logs` 只返回当前用户记录，`DELETE /user-ai/keys` 一键删除全部模型配置与密钥。设置页「密钥与隐私」卡如实写明「服务端代理 ⇒ 服务端必须能解密」，不承诺零知识，并给出存储位置、可见范围、彻底删除方式与轮换策略。
- **多模型**：默认项由 `is_default` 唯一确定；前端模型选择器用 `p:<平台模型id>` / `u:<自带模型id>` 作值，选平台模型时下发 `user_model_id=0` 明确关闭 BYOK，选自带模型时下发对应 id，不改默认项。
- **推理强度**：`auto` 不下发任何推理参数（兼容所有接入点），`off` 关闭，其余档位映射到 pi-agent thinkingLevel。强度参与 provider id 哈希，切换时不会与在途请求串配置。

平台侧同理：`models.json` 的模型可带 `reasoningEffort`（后台模型资料里的「推理强度」），`GET /v1/catalog` 回 `reasoning_effort`；`/v1/chat` 在路由组解析之后用 `modelEffortOf()` 读这一档，作为本轮 `thinkingLevel`，读不到就走默认。`xhigh`/`max` 只有模型声明支持才真正下发，否则 pi 退到 `high`。
- **SSRF**：`core/url_guard.py` 分两级——schema 校验只查字面量（不联网，每轮对话也用得上），点「测试连接」才做 DNS 解析后校验；默认拒绝回环/私网/链路本地/云元数据地址。部署方可设 `BYOK_ALLOW_PRIVATE_NETWORK=true` 放开本机 Ollama，自行承担风险。
- **多用户隔离**：ai-service 用 `owner + sha256(base_url|model|key|effort)` 作 providerId，改配置注册新 id 而不是覆盖旧 id，避免并发请求串 Key；注册上限 200 个，LRU 淘汰。
- **技能包**：只读取 `.md/.markdown/.txt` 文本，ZIP 与文件夹都做路径穿越、符号链接、文件数（200）、压缩后总量（2 MB）、单文件（128 KB）、技能数（20）限制；按每个目录下的 `SKILL.md` 拆成独立技能，附属资料拼进正文，不执行任何脚本。
- **技能注入**：只带启用中的技能，总量上限 8000 字（Node 侧再截一次）；追加在平台规则**之后**并声明「不得覆盖上方规则」，标题换行被清洗，防止用户文本伪造系统级章节。
- **容错**：BYOK/技能属可选增强，配置表缺失、迁移未跑、DB 抖动时 `_resolve_user_ai` 只记一条 warning 并返回 `(None, [])`，绝不阻断对话。
- **范围边界**：目前只作用于简历 Agent 对话；`generate-resume` / `import-resume` 等一次性任务仍走平台模型，以保证结构化输出稳定，也避免改动同步结算链路。

### 7.11 信息库（原子信息）与双通道待确认

信息库把「用户会反复用到的事实」从单份简历里抽出来，属于用户而不是简历。字段模型见 [`data/resume-schema.md`](./data/resume-schema.md) §6，这里只说链路。

```
信息库页面 /workspace/info
  ├─ 手动新增 / 编辑          → POST|PUT /info-fragments
  ├─ 从简历提取（已有经历拆条） → POST /info-fragments/extract（指纹判重，已存在跳过）
  └─ 导入简历（单条 → 简历）    → POST /info-fragments/apply（直接写库，返回最新 resume_data）

对话 /workspace/:id（Flow Agent）
  ├─ search_library   → 只读检索，命中带分类与摘要
  ├─ update_resume     → changes[] / from_library[] 一次提交 → pending_change
  │     └─ 用户点「应用」→ POST .../decision(apply) → 合并后的简历写库
  └─ save_library      → 工具返回待确认的信息条目 → pending_info_fragments
        └─ 用户点「保存到信息库」→ 同一条 decision 接口 → 先入库再（如有）写简历
```

要点：

- **一条消息可同时承载两种待确认内容**（简历快照 + 信息条目），`action_status=pending` 只要任一存在；`resolve_chat_change` 的 apply 分支先用 `apply_proposals()` 入库并把 `pending_info_fragments` 置空，再合并 `optimized_resume_data`；只有信息提案时不会报「没有可写入的修改」。
- **工具不直接写库**：`update-resume`（含 `from_library`）与 `info-fragments/propose` 在 Python 侧只做校验与组装，返回 `optimized_resume_data` / `pending_info_fragments`，落库发生在用户确认之后。
- **幂等**：`fingerprint = sha1(kind + payload)`，重复保存合并标题与标签而不覆盖用户编辑；导入简历按条目内容判重，全部已存在时返回 400 而不是静默造重复条目。
- **可见性**：`search_library` 只返回当前用户、`enabled` 的条目；所有端点都带 `user_id` 过滤，跨用户 `fragment_id` 按 404 处理。
- **前端确认卡**：`ResumeAiChatPanel` 的待确认卡同时看 `optimized_resume_data` 与 `pending_info_fragments`：主按钮是「应用」（只有信息条目时叫「保存到信息库」），有简历改动时多一个次要的「逐条审阅」，另有「放弃」；两种状态的结果文案分开（修改已生效 / 信息已保存到信息库）。

---

## 8. 前端架构

### 8.1 路由与页面

| 路径 | 页面 | 职责 |
|---|---|---|
| `/` | `Home.vue` | 落地页、功能引导、公告；已登录访问时重定向到 `/workspace` |
| `/landing` | `Home.vue` | 营销落地页（登录后仍可访问） |
| `/login`、`/register` | `Login.vue` | 登录/注册合并组件，验证码 |
| `/workspace` | `Workspace.vue` | **工作区外壳**：左侧栏（简历列表 + 用户区）+ 中间对话首页 + 右侧预览空态 |
| `/workspace/:id` | `Workspace.vue` → `ResumeEditor.vue` | **核心页面**，外壳内的编辑器：中栏「Flow Agent 对话 ⇄ 内容编辑」可切换，右栏 A4 预览；中栏默认占工作区 52%（520–1000px 夹取），拖拽分隔条可改并记在 `localStorage` 的 `vitaflow_editor_panel_width` |
| `/workspace/templates` | `Workspace.vue` → `WorkspaceTemplates.vue` | **工作区内的模板中心**：左侧模板网格（搜索 + 分类筛选），右侧选中模板大图 + 「用此模板创建 / 按岗位起稿 / 导入已有简历」，选好模板后带着 `template_id` 进创建流程；网格最多 3 列（`2xl` 才 4 列），右栏占主区 42%（380–560px），预览、标题、入口同宽成列并在面板内垂直居中 |
| `/workspace/resumes` | `Workspace.vue` → `WorkspaceResumeLibrary.vue` | **全部简历**面板：卡片网格、复制、删除、响应式分页（桌面 8 / 窄屏 6）、标题搜索（300ms 防抖）与排序（最近更新 / 最近创建 / 按标题）、无结果空态，创建入口复用 `ResumeCreateFlow`；列表分页状态本地维护，避免与侧栏共用的 store 互相顶掉 |
| `/workspace/info` | `Workspace.vue` → `WorkspaceInfo.vue` | **信息库（原子信息）**：搜索（260ms 防抖）+ 分类 chips（含计数）+ 卡片网格（分类徽标、标题、摘要、标签、使用次数、导入按钮、悬浮编辑/删除）+ 分页；「从简历提取」把已有简历拆成条目，「新增信息」按分类字段表动态建表单，「导入简历」把单条写入指定简历并跳转编辑器 |
| `/resumes` | — | 重定向到 `/workspace/resumes`，兼容旧链接与历史导航 |
| `/resumes/:id/edit` | — | 重定向到 `/workspace/:id`，兼容旧链接 |
| `/templates` | `TemplateGallery.vue` | 仅面向未登录访客的公开模板页（浏览 + 引流到登录）；登录后守卫直接跳到 `/workspace/templates` |
| `/workspace/help` | `Workspace.vue` → `HelpCenter.vue` | 帮助中心，主题导航 + FAQ，快捷入口都指回工作区内部 |
| `/workspace/profile` | `Workspace.vue` → `Profile.vue` | 个人资料、修改密码、意见反馈（`?tab=feedback` 直达反馈页），浅灰底衬托白卡 |
| `/workspace/announcements` | `Workspace.vue` → `AnnouncementHistory.vue` | 公告历史与详情弹窗 |
| `/workspace/ai-records` | `Workspace.vue` → `AiRecords.vue` | Flow Points 余额、兑换、调用记录与消费明细 |
| `/workspace/settings` | `Workspace.vue` → `WorkspaceSettings.vue` | **模型与技能**：多模型配置列表（上限 10、默认项、启停、编辑、删除、探活）+ 内联编辑区（含推理强度选择）+ 技能列表（单文件 / ZIP 包 / 文件夹导入、新建、编辑、启停、删除）+ 密钥与隐私卡（真实加密状态、存储说明、一键清除全部密钥、最近使用记录）；入口在左下用户菜单，按需异步加载 |
| `/help`、`/profile`、`/announcements`、`/ai-records` | — | 旧链接，重定向到对应 `/workspace/*`（`/profile` 保留 query） |
| `/share/:token` | `SharedResume.vue` | 公开分享页，无鉴权 |
| `/workspace/admin` | `Workspace.vue` → `Admin.vue` | 管理后台，`meta.requiresAdmin`；套 `WorkspacePage` 后留在工作区外壳内，内容区按需异步加载 |
| `/admin` | — | 旧链接，重定向到 `/workspace/admin` |

路由守卫：登录态检查 + `requiresAdmin` 前端拦截（**仅体验优化，真正边界在后端**）。

### 8.2 工作区外壳行为

| 维度 | 说明 |
|---|---|
| 布局 | `WorkspaceShell` = 左栏（`WorkspaceSidebar`，桌面常驻）+ 右侧 slot；`h-dvh` 整体不滚动，滚动只发生在其内部容器 |
| 移动端（<768px） | 左栏变抽屉：`fixed` + `translate-x` 滑出，遮罩层 `z-[55]`、抽屉 `z-[60]`（编辑器顶栏 `z-40`、弹窗 `z-[100]`）；开合状态在 `composables/useWorkspaceDrawer.ts`，路由变化 / Esc / 点击遮罩 / 断点回到桌面都会自动收起，抽屉开着时底部胶囊导航隐藏 |
| 会话切换 | 点侧栏会话写 `chatStore.setActiveSession`，编辑器 watch 它换消息；`⌘/Ctrl + Alt + ↑↓` 在当前简历的会话间循环，`⌘/Ctrl + Alt + N` 新建对话（焦点在输入框内不抢键） |
| 搜索 | 侧栏搜索框：简历部分按标题在内存里过滤，会话走后端跨简历搜索（标题或消息内容命中，260ms 防抖）；点结果先 `fetchSessions(resume_id)` 再切会话，否则 active 会被回退成第一条 |
| 置顶 | `POST .../sessions/{id}/pin`；列表顺序 = 置顶优先 → `update_time` 倒序 → id 倒序，前端 `sortSessions` 与后端 SQL 保持同一规则，`PATCH` 后不会出现「钉了却还在下面」 |
| 删除与右键 | 删除对话 / 删除简历都用锚定气泡 `components/ui/dialog/ConfirmPopover.vue`（`z-[210]`）：贴着被删的那一行弹出，优先放右侧，右边不够翻到左侧，两边都不够（窄屏抽屉）落到行下方，小三角始终指向该行；简历行支持右键菜单（打开 / 新建对话 / 复制简历 / 删除简历，`z-[200]`，Esc、点空白、滚轮都会关闭）。删正在编辑的简历前先 `flushSave()` 再删，随后 `router.push('/workspace')` |
| 预览刷新 | `refreshPreviewLatest` 单飞 + 尾随合并：同时只有一个 `/preview` 请求；预览不可见（移动端非「预览」tab、`document.hidden`）时不发请求，只记 `previewDirty`，切回可见时补一次 |
| 创建流程 | 侧栏「新建简历 / AI 生成简历」、工作区首页底部三个入口都只调 `useWorkspaceCreate().openCreate(entry)`；`Workspace.vue` 根部挂 `ResumeCreateFlow`（岗位起稿 / 空白模板 / 导入 / AI 生成）消费意图，创建成功 `emit('created')` → 先 `flushSave()` 再 `push('/workspace/:id')`，全程不离开外壳 |
| 模板中心 | 收编进工作区（`/workspace/templates` → `WorkspaceTemplates.vue`），不再跳全站顶栏的独立页；点「用此模板创建」时 `openCreate('blank', { templateId })` 直接建简历，岗位起稿 / 导入则把该 `template_id` 传给对应接口，不再重复选模板。`/templates` 只保留给未登录访客 |
| 内容页 | 帮助中心 / 个人中心 / 公告 / Flow Points / 信息库统一走 `WorkspacePage.vue`（滚动容器 + 移动端抽屉入口 + 可选浅灰底），路由挂在 `/workspace/*` 下，`Workspace.vue` 用 `panel` 计算属性决定渲染哪一个；这些页面按需异步加载，不进首屏包 |
| 公告弹窗 | 挂在工作区根部（`Workspace.vue`），除编辑器外都会弹；空标题不弹。此前只挂在「全部简历」面板上，用户不进那个面板就永远看不到公告 |
| 全部简历 | 收编进工作区（`/workspace/resumes` → `WorkspaceResumeLibrary.vue`），分页、复制、删除都在面板内完成；侧栏只拉最近 50 份，超出时列表底部出现「查看全部 N 份」入口。面板的列表分页状态独立于 `resume` store，增删后回写一次 store，侧栏不会因为面板把 `page_size` 改成 8 而只剩 8 条 |

### 8.3 目录职责

| 目录 | 内容 |
|---|---|
| `views/` | 路由级页面，负责取数、权限判断、流程编排 |
| `components/editor/` | section 表单、条目列表、字段编辑、富文本、拖拽排序 |
| `components/preview/` | 预览容器、缩放、分页提示 |
| `components/ai/` | 对话面板、消息气泡、`ThinkingProcess.vue`（思考过程可视化）、变更确认卡片、提问卡片、工具进度 |
| `components/templates/` | 模板缩略预览（`TemplatePreview.vue`）；实际简历渲染由后端 Jinja2 模板负责，历史上的 `ClassicTemplate.vue` / `ModernTemplate.vue` / `TechTemplate.vue` 已随旧渲染路径一并删除 |
| `components/workspace/` | 工作区首页（对话起始态）、模板中心面板（`WorkspaceTemplates.vue`）、全部简历面板（`WorkspaceResumeLibrary.vue`）、内容页外壳（`WorkspacePage.vue`）、右侧预览空态 |
| `components/admin/` `announcement/` `resume/` `home/` `layout/` `common/` `ui/` | 分域组件；`resume/` 下 `ResumeCreateFlow.vue` 承载全部创建流程（工作区与 `/resumes` 共用）；`layout/` 下 `MainLayout.vue` 只服务公开页（`/`、`/landing`、`/templates`），`WorkspaceShell.vue` / `WorkspaceSidebar.vue` / `WorkspaceUserMenu.vue` 是工作区外壳；`AppLayout.vue` 这个曾经的空壳包装器已删除 |
| `composables/` | `useWorkspaceBridge.ts`：侧栏切换简历前调用编辑器注册的 `flushSave`，避免防抖间隙丢改动；`useWorkspaceDrawer.ts`：移动端侧栏抽屉的开合状态；`useWorkspaceCreate.ts`：创建入口意图分发（`choice` / `starter` / `ai` / `import` / `blank`），可带 `templateId` 跳过模板选择 |
| `stores/` | 4 个：`user`（登录态、积分）、`resume`（列表）、`editor`（当前简历、脏标记、保存、预览刷新）、`chat`（每份简历的会话列表与当前会话，侧栏与编辑器共用） |
| `api/` | 每个业务域一个文件；`request.ts` 是 axios 封装，`ai.ts` 是流式专用 |
| `types/` | 与后端 schema 对齐的 TS 类型（`resume.ts` / `ai.ts` / `api.ts` / `user.ts`） |
| `utils/` | `aiDiff.ts` / `wordDiff.ts`（变更 diff）、`resumePagination.ts`（分页估算）、`resumePresets.ts`（预设）、`templateNames.ts`、`toast.ts` |
| `lib/` | `markdown.ts` 渲染、通用工具 |

### 8.4 双通道请求

- **普通接口**：`api/request.ts` axios 实例 → 注入 token → 401 跳登录 → 解包 `{code,message,data}` → 统一中文错误提示。
- **AI 流式接口**：`api/ai.ts` 原生 `fetch` + `ReadableStream` + 逐行 `JSON.parse`，手动处理 401 与中断，逐事件回调给组件。

### 8.5 编辑器状态流

表单编辑 → 更新 `editor` store 中的 `resumeData` → 标脏 → 防抖自动保存（`PUT /resumes/{id}`）→ 预览区通过 `POST /resumes/{id}/preview-html` 传入**当前未保存数据**刷新，保证预览与表单实时一致。

AI 对话产生的 pending change 在右侧面板展示 diff，用户「应用」后调用 `decision` 接口，后端合并落库并返回新简历，前端整体替换 store 数据。

---

## 9. 预览与导出链路

```
resume_data + template_id + template_config
        │
        ├─► preview_service   ─ Jinja2 + backend/app/templates/resume/{id}.html → HTML
        │        ├─► 浏览器预览（iframe / 新窗口）
        │        ├─► 分享页 SharedResume（同源 HTML）
        │        └─► pdf_export_service ─ Playwright Chromium → PDF（带缓存指纹，落盘）
        ├─► resume_json_export ─ json.dumps → .json（完整快照：标题/语言/模板/样式配置 + resume_data）
        │        └─► 回填：POST /resumes/import-file 上传同一个 .json → 直接建新简历，不走 AI、不扣积分
        └─► word_export_service ─ python-docx → .docx（独立实现，需人工对齐模板内容）
```

PDF 与 JSON 是两条形态完全不同的导出：**PDF 落盘并带缓存指纹**（Chromium 渲染贵，同一份内容重复导出要复用）；**JSON 不落盘、流式返回**（纯序列化，没有渲染成本也没有复用价值，缓存只会留下垃圾文件）。因此 JSON 的 `export_records.file_path` 写占位值 `inline:json`，表示后台这条记录没有可下载文件。

JSON 快照**故意不含** `id` / `user_id` / `share_token` / `create_time`：导入是新建一份简历，带 id 会冲突，带分享 token 等于把凭证塞进一个会被转发的文件。回填时 `template_id` 若已被删除则回落 `tech`，`template_config` 过白名单收敛（它会被插进 `<style>`），`resume_data` 仍走 `normalize_resume_document` 补 id 与 schema 版本。

12 套模板：`classic`、`tech`、`modern`、`modern_clean`、`minimal_light`、`minimal_mono`、`blue_timeline`、`elegant_line`、`editorial_serif`、`executive_panel`、`portfolio_cards`、`compact_matrix`，共用 `style-base.css` 与 `partials/`。

改模板时必须同时考虑：浏览器预览、PDF、Word、分享页四处一致性。PDF 渲染依赖 Chromium、字体、资源 URL 与分页，容器内需置空 `PDF_CHROMIUM_EXECUTABLE_PATH` 以使用 Playwright 自带浏览器。

---

## 10. 部署

```bash
# 开发：只起中间件与 ai-service，前后端跑宿主机（保留热更新）
docker compose up -d

# 全栈：多起 backend-migrate → backend → frontend(Nginx)
docker compose --profile full up -d --build
```

- `backend-migrate` 是一次性任务（`alembic upgrade head`），backend 等它成功才启动。
- ai-service 默认 `PI_PROVIDER=faux`（确定性模拟，无网络依赖，便于演示与测试）；生产设 `real` 并注入 Key。
- ai-service 显式配置公共 DNS（223.5.5.5 / 119.29.29.29），因为容器内置 DNS 转发偶发 8s+ 超时，是「空闲后首回合首字 10s+」的根因。
- 常用环境变量：`AI_SERVICE_URL`、`AI_SERVICE_TOKEN`、`AI_TURN_TIMEOUT_MS`（compose 默认 300000）、`PI_FASTAPI_BASE_URL`、`SEARCH_PROVIDER`/`SEARCH_API_KEY`、`PDF_RENDERER`、`STORAGE_PROVIDER`。

---

## 11. 开发约束（改动前必读）

1. 路由不堆业务，业务进 `services/`；视图不堆表单，拆 `components/`。
2. 数据库结构变更必须新增 Alembic 迁移，不改旧迁移；不使用物理外键。
3. 响应结构保持 `{code, message, data}`；异常走统一处理器。
4. 新增配置必须同步 `.env.example` 与文档。
5. AI 相关改动必须同时检查：工具校验、合并逻辑、`resume_data` schema、渲染模板、前端类型。
6. AI 输出进入 HTML 前必须过 `rich_text_service` 清理，防 XSS。
7. 不硬编码任何 Key / 密码 / 密钥；管理员能力必须后端校验。
8. 修改简历数据结构时，六处同步：`core/resume_schema.py`（字段表 + `SCHEMA_VERSION`）、TS 类型、默认数据、表单组件、渲染模板、导出服务；字段表是唯一事实源，不要在 Node 或前端另抄一份。
9. 前端改动至少 `npm run build` + `npm test`（vitest，配置 `frontend/vitest.config.ts`）；后端至少 `pytest -q`（`backend/requirements-dev.txt` 提供 pytest）；ai-service 至少 `npm run typecheck && npm test`；涉及预览/导出的要人工验证无空白、无分页错位。
10. 不自动提交、不自动删文件、不清理用户改动。
11. 新增用户可控的出站 URL / 密钥 / 提示词内容，必须同时做：加密落库且不回明文（密钥用独立口令，禁止复用 JWT 弱默认）、SSRF 校验、日志与异常脱敏、可查询的使用审计与一键清除、提示词注入边界（追加在系统规则之后 + 长度上限）、以及计费口径（是否消耗平台积分）。

---

## 12. 已知技术债与风险

| 项 | 位置 | 说明 |
|---|---|---|
| 超大文件 | `admin.py` 2157 行、`ai_chat_service.py` 1218 行、`Admin.vue` 1599 行、`ResumeEditor.vue` 1527 行 | 与「路由不堆业务、视图不堆表单」的自我约束有张力；`ai_chat_service.py` 同时管会话、上下文、模型选择、校验、合并、决策，改动易牵一发动全身 |
| settings 未固定 | `ai-service/src/agent.ts` | 两处 `createAgentSession` 都未传 `settingsManager`，会读 `<cwd>/.pi-agent/settings.json`，导致 compaction/retry 行为受部署目录影响、不可复现。建议改 `SettingsManager.inMemory({ compaction, retry })` |
| thinking 逐 token 穿两跳 | `agent.ts` → NDJSON → FastAPI → 浏览器 | 每次 1–3 字符，一次响应上百条，每条都要一次 `JSON.parse` + `res.write` + 前端状态更新。建议 Node 侧 80–120ms 节流合并；同时 `res.write` 未处理背压（未监听 `drain`） |
| 工具回调往返 | 每个工具一次跨进程 HTTP | 一轮可能 3 次往返。优化方向：上下文里预置 section 只读快照，让 `get_resume_context` 本地返回 |
| 超时默认值不一致 | `config.ts` 默认 60000ms，`docker-compose.yml` 传 300000ms | 不走 Docker 直接 `npm run dev` 时，导入整份简历（实测 60s+）会撞默认超时 |
| 计费收尾仍阻塞 | `server.ts` 在 `res.end()` 后 `await reportUsage` | 注释写 fire-and-forget，实际占住请求收尾；FastAPI 慢时会拖长 Node 连接生命周期 |
| 静态共享令牌 | `AI_SERVICE_TOKEN` | §14 决策 1 已列为开放问题。当前「内网 + 无 shell 工具 + Python 侧兜底校验」下风险可控；升级方向是短时签名（时间戳 + HMAC） |
| BYOK 未覆盖一次性任务 | `api/ai.py`、`api/resumes.py` | 自定义模型目前只作用于 Agent 对话；生成/导入任务仍走平台模型。扩展时需同步改造各处 `precheck_flow_points` / `settle_task_flow_points`，否则会出现「用用户 Key 却扣平台积分」 |
| `ui/input` 等旧控件 | `components/ui/` | 本轮已把 Input/Textarea 从 12px 收敛到 8px 控件档；`Select` 的 ghost 类、`ConfirmDialog` 里的 `rounded-xl` 按钮等仍有零散发散，遇到即按 §1 圆角阶梯校准 |
| 原子信息落点靠约定 | `FRAGMENT_KINDS.preset_type` / `info_fragment_service.py` | 落到 `custom_sections` 的分类靠 `preset_type` 字符串匹配模块；新增分类忘配（或写错）preset 时不会报错，只会退化成独立的「自定义」模块。新增分类时按 [`data/resume-schema.md`](./data/resume-schema.md) §6.1 表逐项核对 |
| 简历字段表与实际表单仍有漂移风险 | `resume_schema.py` ↔ `SectionListForm.vue` | 字段表是契约，但前端条目默认字段是手写的 `Object.assign`。新增字段后若忘了改表单，新字段不会出现在编辑器里（数据仍在）。已有 `ResumeSchemaTest` 守住后端，尚无跨端一致性测试 |

---

## 13. 术语表

| 术语 | 含义 |
|---|---|
| 模型目录（catalog） | `ai-service/.pi-agent/models.json`，定义 provider/model 的能力与 Key 引用 |
| 模型定价（model pricing） | MySQL `ai_model_configs` 一行（`model_ref` 唯一），业务侧「这个模型多少钱、是否上架、是不是默认、带几条上下文」 |
| 模型引用（model_ref） | 策略指向目录中的具体 `provider/model` 的字符串 |
| 待确认变更（pending change） | AI 产出但未应用的简历修改，挂在 assistant 消息上，需用户确认 |
| 原子信息（fragment） | 信息库里的一条事实（一段实习、一个项目、一个奖项…），属于用户而非简历，可被检索与导入 |
| 写入契约（resume schema） | `core/resume_schema.py` 声明的模块/字段表，同时是前端、AI 与导入的规范化依据；`schema_version` 随字段表递增 |
| 起手式（resume starter） | 按行业预置的简历骨架与示例内容，新建时可选 |
| 积分（flow points） | 平台内计费单位，AI 调用与部分功能消耗 |
| 回合（turn） | 一次用户输入到 Agent 结束（`agent_settled`）的完整过程 |
| phase | 流式事件，用于提示「正在读取简历 / 正在改写 / 正在校验」等阶段 |
| section | 简历的模块单元（education、work、projects…），每条目有稳定 id |
| template_config | 模板的配色变量（theme_color、bg_color、icon_color），存于简历 |

---

## 14. 关键架构决策

AI 子系统改造中的 5 个关键取舍，每条记录「被否决的方案」及原因，便于后续重构时知道前人试过什么。完整动机与迁移路径见 [`architecture/pi-agent重构设计.md`](./architecture/pi-agent重构设计.md)。

> 代码注释中的 `ADR-000N`（如 `catalog.ts` 的 `ADR-0005`）指向本表第 N 条。原 `docs/adr/` 目录已合并至此，编号保持不变以便与历史提交对应。

| # | 决策 | 被否决的方案与原因 |
| --- | --- | --- |
| 1 | **Python 后端保留，AI 能力拆成 Node sidecar** | **全量迁 Node**：会丢掉 Python 生态（Playwright 导出、WeasyPrint、pytesseract），工程量巨大；**混合双栈**（Node 只管对话、其余仍在 Python 进程内调 LLM）：两套 LLM 客户端并存，计费与日志割裂，与「完全重写」目标冲突。sidecar 边界清晰、pi-agent 升级只影响单一服务；代价是双语言部署 + 服务间需要轻量认证 |
| 2 | **统一对话入口，取代 9 个独立功能 API** | **全部保留并 Agent 化**：单步结构化生成套 Agent 循环是负收益；**仅迁移 resume-chat 试点**：与「一次重写收敛所有入口」的目标不符。改为高频操作封装成对话内快捷指令（`/润色` `/翻译` `/优化` `/生成`），其余靠多模态自然对话触发；简历评分单次耗时 51s、独立价值低，直接移除 |
| 3 | **MySQL 是会话事实源，Agent 会话按请求重建** | **pi SessionManager 落库 + 与现有表双写**：两份事实源需要同步协议，一致性与调试成本都高。改用 `SessionManager.inMemory()`，简历快照、历史消息、待确认变更由 FastAPI 按 `context_messages` 注入，请求结束即弃——多用户安全、UI 与决策流零改动、AI 服务可水平扩展；代价是每请求重建上下文，但上下文本就来自 DB 查询，与原先同构。pi 的跨请求记忆（compaction、历史压缩）因此不使用，长对话裁剪由 `context_messages` 上限控制 |
| 4 | **section 级工具 + 回调 Python 校验** | **字段级工具**：粒度最细但轮次多，Agent 循环反而更慢；**纯提案制**（工具只返回提案、不落库）：延迟收益打折，且与现有 pending_change 决策流重复。旧链路让模型整份重发 `optimized_resume_data`（≈10KB JSON），延迟由输出 token 决定（实测 30~50s）；section 级把单次输出降到 KB 级、目标 ~5s，信任边界仍在服务端 |
| 5 | **模型目录归 ai-service，定价留 MySQL** | **目录继续手工编辑文件**：改模型要登服务器改 `models.json`，与「后台全管 AI」的运维预期冲突；**Key 存环境变量**：新增 Key 要改容器环境变量并重启，无法热生效。改为后台通过目录 CRUD 全量管理、Key 明文写入 `models.json`（换 Key 即时生效，不进后端 DB 与 `.env`）；代价是密钥明文落盘，依赖部署环境对 `.pi-agent/` 目录的文件权限保护 |

> 决策 4 的工具粒度后来被 [`ai/resume-agent.md`](./ai/resume-agent.md) 收敛为单一 `update_resume`，但「工具做增量、Python 做校验」的结论不变。

## 15. 相关文档

完整文档索引见 [`README.md`](./README.md)。其中：端到端链路（含行号）见 [`ai/ai-call-flows.md`](./ai/ai-call-flows.md)；提示词分层、内置技能与工具契约见 [`ai/resume-agent.md`](./ai/resume-agent.md)；重构动机与取舍见 [`architecture/pi-agent重构设计.md`](./architecture/pi-agent重构设计.md)。
