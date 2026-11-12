# VitaFlow 项目架构与功能说明

> 本文档用于向其他 AI / 协作者完整交代 VitaFlow 的架构、模块边界、领域模型与运行约束。
> 读完本文应能独立定位代码、判断改动影响面，而不需要先通读仓库。
> 文末附「术语表」与「已知技术债」，避免误读和重复踩坑。

---

## 1. 项目定位

VitaFlow（中文名「简历流」）是一个面向求职者的**智能简历平台**：在线编辑简历 → AI 对话式优化 → 多模板预览 → 导出 PDF / Word → 生成分享链接。

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

> 注意：`AGENTS.md` 中仍写着后端使用 LangChain/LangGraph，**这是过时描述**。旧 AI 管线已退役，`requirements.txt` 中已无 langchain 依赖，`services/ai/` 仅剩 `token_usage.py`。

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

**关键设计约束**：前端**不直连** ai-service；ai-service **不连数据库**；模型 API Key 只存在于 ai-service 进程。

---

## 4. 仓库结构

```
vitaflow/
├── AGENTS.md              # 仓库级开发约束（代码代理必读，但 AI 技术栈段落已过时）
├── CONTEXT.md             # 领域术语表
├── README.md              # 总入口
├── docker-compose.yml
├── backend/               # FastAPI，约 12.6k 行 Python
│   ├── app/
│   │   ├── api/           # 12 个路由模块，只做参数解析与编排
│   │   ├── services/      # 22 个业务服务
│   │   ├── models/        # 8 个 SQLAlchemy 模块
│   │   ├── schemas/       # 10 个 Pydantic 模块
│   │   ├── core/          # config / security / response / exceptions / database
│   │   ├── templates/resume/  # 12 套 Jinja2 简历模板
│   │   └── main.py
│   ├── alembic/versions/  # 35 个迁移
│   └── tests/             # 21 个测试文件
├── frontend/              # Vue 3，约 16k 行
│   └── src/{views,components,stores,api,router,types,utils}/
├── ai-service/            # Node 旁路服务
│   ├── src/{server,agent,prompt,commands,taskPrompts,catalog,billing,usage,config,real,faux}.ts
│   ├── src/tools/         # 6 个 Agent 工具
│   └── .pi-agent/models.json   # 模型目录（能力清单 + API Key 引用）
└── docs/
    ├── adr/               # 架构决策记录 0001~0003
    ├── agents/            # issue tracker 与 domain doc 约定
    ├── pi-agent重构设计.md
    └── ai-call-flows.md   # 端到端链路，标注到行号
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
`GET /resumes`、`POST /resumes`、`POST /resumes/from-starter`、`POST /resumes/import-file`（上传 docx/pdf 解析成简历）、`GET|PUT|DELETE /resumes/{id}`、`POST /resumes/{id}/duplicate`、`POST /resumes/{id}/versions`（存版本快照）、`GET|PUT /resumes/{id}/share`（开关与 token）、`GET|POST /resumes/{id}/preview-html`（HTML 预览，POST 支持传入未保存的草稿）、`GET /resumes/{id}/preview-pdf`

**导出 `exports.py`**
`POST /exports/{resume_id}/export/pdf`、`POST /exports/{resume_id}/export/word`

**AI `ai.py`（最核心，约 900 行）**
- 能力与记录：`GET /ai/capability`、`GET /ai/records`、`GET /ai/histories`
- 积分：`GET /ai/flow-points`、`GET /ai/flow-points/transactions`、`POST /ai/flow-points/redeem`
- 流式：`POST /ai/generate-resume/stream`（按目标岗位生成整份简历）、`POST /ai/resume-chat/{resume_id}/messages/stream`、`POST /ai/resume-chat/{resume_id}/messages/{message_id}/regenerate/stream`
- 会话（一份简历多条对话）：`GET /ai/resume-chat/{resume_id}/sessions`（置顶优先 + 含条数与末条预览）、`POST .../sessions`、`PATCH .../sessions/{session_id}`（重命名）、`POST .../sessions/{session_id}/pin`（置顶/取消）、`DELETE .../sessions/{session_id}`
- 会话搜索（跨简历，命中标题或任意消息内容）：`GET /ai/resume-chat/sessions/search?q=&limit=`，结果带 `resume_title`
- 变更决策：`POST /ai/resume-chat/{resume_id}/messages/{message_id}/decision`（apply / reject）、`DELETE /ai/resume-chat/{resume_id}/messages?session_id=`
- 历史：`GET /ai/resume-chat/{resume_id}/messages?session_id=`（省略 session_id 时落在该简历最近一次会话，兼容旧前端）
- **服务间回调（供 ai-service 调用）**：`POST /ai/service/tool/update-basics-field`、`POST /ai/service/tool/update-section`、`POST /ai/service/tool/propose-change`、`GET /ai/service/tool/get-resume-context`、`POST /ai/service/tool/search-evidence`、`POST /ai/service/usage`

**其余**
- `shares.py`：`GET /shares/{token}`（公开分享，无需登录）
- `templates.py`：`GET /templates`、`GET /templates/{id}`
- `files.py`：`POST /files/upload-avatar`、`/upload-announcement-image`、`/upload-ai-chat-image`、`GET /files/font-assets/{name}`、`GET /files/{object_name}`（存储代理）
- `feedback.py`：`POST /feedback`、`GET /feedback`
- `announcements.py`：`GET /announcements`、`GET /announcements/current`、`GET /announcements/{id}`、`POST /announcements/{id}/dismiss`
- `resume_starters.py`：`GET /resume-starters`（按行业预置的简历起手式）
- `admin.py`（约 2157 行，管理员专用）：公告 CRUD、仪表盘、站点设置、反馈处理与回信、积分批量发放/撤销、积分流水、用户管理、简历总览、AI 任务、AI 模型策略 CRUD、**模型目录代理**（`/ai-catalog*`，转发到 ai-service）、积分规则、兑换码（含导入/导出）、导出记录、模板管理、起手式与行业模板管理

### 5.3 服务层职责

| 服务 | 作用 |
|---|---|
| `ai_service_client.py` | 到 ai-service 的 HTTP 客户端：`/v1/chat` 与 `/v1/task`，解析 NDJSON 流并转成 SSE/回调；统一注入 `AI_SERVICE_TOKEN` |
| `ai_chat_service.py` | 1218 行，AI 对话总控：会话/消息持久化、上下文构造、模型策略选择、工具结果校验、增量合并、变更决策、后台滚动摘要 |
| `ai_task_service.py` | 一次性任务（生成简历、导入解析、摘要压缩）的调用与结果落库 |
| `ai_config_service.py` | `ai_model_configs` 业务策略 CRUD，与 ai-service 模型目录做 `model_ref` 关联 |
| `flow_points_service.py` | 积分账户、扣减、发放、流水、兑换码核销 |
| `preview_service.py` | Jinja2 渲染简历 HTML，12 套模板共用，注入 `template_config`、字体、图标 |
| `pdf_export_service.py` | Chromium(Playwright) 渲染 PDF，带缓存指纹（URL + 模板 + 数据 hash） |
| `word_export_service.py` | python-docx 生成 Word，需与 HTML 模板保持内容一致 |
| `resume_import_service.py` | 上传 docx/pdf → 抽取文本 → 交给 AI 解析成结构化简历 |
| `resume_normalization.py` / `resume_locale.py` | 简历 JSON 规范化（补默认字段、修类型）与多语言字段处理 |
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
| `ai_chat_messages` | 消息；assistant 消息可带 `optimized_resume_data`(待确认变更)、`suggestions`(变更摘要)、`action_status`(pending/applied/rejected)、usage 字段 |
| `ai_tasks` | 一次性任务记录（类型、输入、输出、状态、错误、耗时、token） |
| `ai_model_configs` | **业务策略**：`model_ref` 指向模型目录中的 provider/model；含 `name`、`context_messages`、`is_chat_selectable`、`sort_order`、`is_active`，以及三种费率 `chat_points_per_call` / `chat_points_per_million_input_tokens` / `chat_points_per_million_output_tokens` |
| `flow_point_rules` | 各功能（生成/对话/导出…）的积分单价 |
| `flow_point_transactions` | 积分流水（增减、原因、关联对象） |
| `flow_point_redeem_codes` / `flow_point_redeem_records` | 兑换码与核销记录 |
| `app_settings` | 站点级开关（注册开关、公告、AI 默认模型等） |

> 约定：**MySQL 表不使用物理外键**，关联靠业务字段 + 应用层校验。所有结构变更必须新增 Alembic 迁移，不改旧迁移。

---

## 6. 领域模型：简历 JSON

这是全项目最重要的数据结构，前端表单、AI 工具、渲染模板、导出服务都以它为契约。

```jsonc
{
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
  "work":      [{ "id": "work_1", "company": "", "role": "", "start_date": "",
                  "end_date": "", "description": "", "highlights": [] }],
  "projects":  [{ "id": "project_1", "name": "", "role": "", "start_date": "",
                  "end_date": "", "tech_stack": "", "description": "", "highlights": [] }],
  "awards":    [{ "id": "award_1", "title": "", "date": "", "description": "" }],
  "custom_sections": [{ "id": "", "title": "", "items": [] }],
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
- 列表型 section 的每个条目都有稳定 `id`，AI 的增量更新**按 id 定位**，避免整段覆盖。
- `layout` 控制顺序、显隐、标题文案，模板只负责视觉，不决定结构。
- `custom_sections` 支持用户自定义模块，工具与模板需兼容。

---

## 7. AI 子系统（本项目复杂度中心）

### 7.1 为什么有独立进程

pi-agent 是 TypeScript SDK，无法在 Python 进程内运行；把整个后端改写成 Node 也不划算（认证、ORM、导出、存储生态都在 Python）。因此采用 **sidecar 模式**：FastAPI 拥有全部业务与数据权限，Node 只做一个「无状态 Agent 执行器」。决策记录见 `docs/adr/0001`。

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
  - `resume`：id、title、目标岗位、简历意图摘要
  - `context_summary`：滚动压缩摘要（超出窗口的旧消息）
  - `history`：最近 N 条消息（N 取模型策略 `context_messages`，夹在 1..40）
  - `pending_change`：是否存在待确认变更及其摘要
- 长对话压缩：回合结束后 `asyncio.create_task` 起后台任务，调 `/v1/task` 的 `chat_summary` 增量合并旧消息进 `ai_chat_sessions.summary`。

### 7.4 事件协议（Node → NDJSON → FastAPI → 浏览器）

`start` → `phase`(阶段提示) → `delta`(正文增量) / `thinking`(思考增量) / `tool`(工具开始/结束/进度) → `result`(最终结果与 usage) → `error`。

- 只订阅 subscribe 可见的事件：`message_update`(text_delta/thinking_delta)、`message_end`(usage)、`tool_execution_start/end`、`agent_settled`。
- 结束信号用 `agent_settled`（不是 `agent_end`），并在 `finally` 里二次兜底 `sendResult`，避免压缩/重试流程未完成就判结束。
- 前端 `api/ai.ts` 用原生 `fetch` + `ReadableStream` 逐行解析，**绕开 axios**（axios 无法增量读流）；普通接口仍走 axios 拦截器。

### 7.5 系统提示词与命令

- `prompt.ts`：显式覆盖 pi 的「编码助手」人设，清空 `appendSystemPromptOverride`，关闭 `noContextFiles`/`noSkills`，注入简历上下文、事实边界规则、招聘写作标准。
- `commands.ts`：注册 5 个斜杠命令——`/润色`、`/翻译`、`/JD优化 <岗位描述>`、`/生成 <目标岗位>`、`/开场白`。通过 `promptsOverride` 注入虚拟文件系统。
- **关键坑**：传了 `resourceLoader` 之后必须 `await loader.reload()`，否则上述命令全部静默失效。代码中已显式调用。
- `taskPrompts.ts`：三类一次性任务 `generate_resume` / `import_resume` / `chat_summary`，各自 system prompt + 约束（只输出合法 JSON、禁编造事实、禁编造量化指标）。

### 7.6 工具清单（白名单，共 6 个）

| 工具 | 作用 | 是否回调 FastAPI |
|---|---|---|
| `update_basics_field` | 改 basics 单字段 | 是 → `/service/tool/update-basics-field` |
| `update_section` | 按 item id 增量更新某 section | 是 → `/service/tool/update-section` |
| `propose_change` | 提出整份简历变更方案 | 是 → `/service/tool/propose-change` |
| `get_resume_context` | 读取简历完整内容 | 是 → `/service/tool/get-resume-context`（GET） |
| `ask_user_questions` | 向用户提问（缺信息时澄清） | 否，仅产出结构化问题 |
| `web_search` | 联网搜索（公司/岗位/技术事实核查） | 是 → `/service/tool/search-evidence`；仅当配置 `SEARCH_PROVIDER` 时注册 |

**`read` / `bash` / `edit` / `write` 全部禁用**。多用户 Web 场景下 `bash` 是明确的安全风险，这一条不可回退。

### 7.7 四层防御：AI 为什么不直接改库

1. **工具不持数据库句柄**：Node 侧只做参数整理，增量 POST 给 Python。
2. **Python 侧加载真实简历**：不信任 Node 传来的现状，避免「模型基于过期快照改」。
3. **限定合并**：按 item id 合并，不允许新增未提供的条目 id，不允许越界改 section。
4. **数值防幻觉校验**：`_unverified_numeric_claims()` 比对原文，模型新增的百分比/数量/QPS 等量化结果会被标记或剔除。
5. **落成 pending**：结果写入 `ai_chat_messages.optimized_resume_data` + `action_status=pending`，用户点「应用」才执行合并写库。

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
- 两条路径语义不同（异步回调 vs 同步结算），新增任务类型时必须明确走哪一条。

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
| `/workspace/resumes` | `Workspace.vue` → `WorkspaceResumeLibrary.vue` | **全部简历**面板：卡片网格、复制、删除、响应式分页（桌面 8 / 窄屏 6），创建入口复用 `ResumeCreateFlow`；列表分页状态本地维护，避免与侧栏共用的 store 互相顶掉 |
| `/resumes` | — | 重定向到 `/workspace/resumes`，兼容旧链接与历史导航 |
| `/resumes/:id/edit` | — | 重定向到 `/workspace/:id`，兼容旧链接 |
| `/templates` | `TemplateGallery.vue` | 仅面向未登录访客的公开模板页（浏览 + 引流到登录）；登录后守卫直接跳到 `/workspace/templates` |
| `/workspace/help` | `Workspace.vue` → `HelpCenter.vue` | 帮助中心，主题导航 + FAQ，快捷入口都指回工作区内部 |
| `/workspace/profile` | `Workspace.vue` → `Profile.vue` | 个人资料、修改密码、意见反馈（`?tab=feedback` 直达反馈页），浅灰底衬托白卡 |
| `/workspace/announcements` | `Workspace.vue` → `AnnouncementHistory.vue` | 公告历史与详情弹窗 |
| `/workspace/ai-records` | `Workspace.vue` → `AiRecords.vue` | Flow Points 余额、兑换、调用记录与消费明细 |
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
| 预览刷新 | `refreshPreviewLatest` 单飞 + 尾随合并：同时只有一个 `/preview` 请求；预览不可见（移动端非「预览」tab、`document.hidden`）时不发请求，只记 `previewDirty`，切回可见时补一次 |
| 创建流程 | 侧栏「新建简历 / AI 生成简历」、工作区首页底部三个入口都只调 `useWorkspaceCreate().openCreate(entry)`；`Workspace.vue` 根部挂 `ResumeCreateFlow`（岗位起稿 / 空白模板 / 导入 / AI 生成）消费意图，创建成功 `emit('created')` → 先 `flushSave()` 再 `push('/workspace/:id')`，全程不离开外壳 |
| 模板中心 | 收编进工作区（`/workspace/templates` → `WorkspaceTemplates.vue`），不再跳全站顶栏的独立页；点「用此模板创建」时 `openCreate('blank', { templateId })` 直接建简历，岗位起稿 / 导入则把该 `template_id` 传给对应接口，不再重复选模板。`/templates` 只保留给未登录访客 |
| 内容页 | 帮助中心 / 个人中心 / 公告 / Flow Points 统一走 `WorkspacePage.vue`（滚动容器 + 移动端抽屉入口 + 可选浅灰底），路由挂在 `/workspace/*` 下，`Workspace.vue` 用 `panel` 计算属性决定渲染哪一个；四个页面按需异步加载，不进首屏包 |
| 公告弹窗 | 挂在工作区根部（`Workspace.vue`），除编辑器外都会弹；空标题不弹。此前只挂在「全部简历」面板上，用户不进那个面板就永远看不到公告 |
| 全部简历 | 收编进工作区（`/workspace/resumes` → `WorkspaceResumeLibrary.vue`），分页、复制、删除都在面板内完成；侧栏只拉最近 50 份，超出时列表底部出现「查看全部 N 份」入口。面板的列表分页状态独立于 `resume` store，增删后回写一次 store，侧栏不会因为面板把 `page_size` 改成 8 而只剩 8 条 |

### 8.3 目录职责

| 目录 | 内容 |
|---|---|
| `views/` | 路由级页面，负责取数、权限判断、流程编排 |
| `components/editor/` | section 表单、条目列表、字段编辑、富文本、拖拽排序 |
| `components/preview/` | 预览容器、缩放、分页提示 |
| `components/ai/` | 对话面板、消息气泡、`ThinkingProcess.vue`（思考过程可视化）、变更确认卡片、提问卡片、工具进度 |
| `components/templates/` | 模板选择与缩略预览（**注意：`ClassicTemplate.vue`/`ModernTemplate.vue`/`TechTemplate.vue` 在 `src` 内已无引用，属旧渲染路径残留**） |
| `components/workspace/` | 工作区首页（对话起始态）、模板中心面板（`WorkspaceTemplates.vue`）、全部简历面板（`WorkspaceResumeLibrary.vue`）、内容页外壳（`WorkspacePage.vue`）、右侧预览空态 |
| `components/admin/` `announcement/` `resume/` `home/` `layout/` `common/` `ui/` | 分域组件；`resume/` 下 `ResumeCreateFlow.vue` 承载全部创建流程（工作区与 `/resumes` 共用）；`layout/` 下 `MainLayout.vue` 只服务公开页（`/`、`/landing`、`/templates`），`WorkspaceShell.vue` / `WorkspaceSidebar.vue` / `WorkspaceUserMenu.vue` 是工作区外壳；`AppLayout.vue` 这个曾经的空壳包装器已删除 |
| `composables/` | `useWorkspaceBridge.ts`：侧栏切换简历前调用编辑器注册的 `flushSave`，避免防抖间隙丢改动；`useWorkspaceDrawer.ts`：移动端侧栏抽屉的开合状态；`useWorkspaceCreate.ts`：创建入口意图分发（`choice` / `starter` / `ai` / `import` / `blank`），可带 `templateId` 跳过模板选择 |
| `stores/` | 4 个：`user`（登录态、积分）、`resume`（列表）、`editor`（当前简历、脏标记、保存、预览刷新）、`chat`（每份简历的会话列表与当前会话，侧栏与编辑器共用） |
| `api/` | 每个业务域一个文件；`request.ts` 是 axios 封装，`ai.ts` 是流式专用 |
| `types/` | 与后端 schema 对齐的 TS 类型（`resume.ts` / `ai.ts` / `api.ts` / `user.ts`） |
| `utils/` | `aiDiff.ts` / `wordDiff.ts`（变更 diff）、`resumePagination.ts`（分页估算）、`resumePresets.ts`（预设）、`printResume.ts`、`toast.ts` |
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
        │        └─► pdf_export_service ─ Playwright Chromium → PDF（带缓存指纹）
        └─► word_export_service ─ python-docx → .docx（独立实现，需人工对齐模板内容）
```

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
8. 修改简历数据结构时，五处同步：TS 类型、默认数据、表单组件、渲染模板、导出服务。
9. 前端改动至少 `npm run build`；后端至少启动级检查；涉及预览/导出的要人工验证无空白、无分页错位。
10. 不自动提交、不自动删文件、不清理用户改动。

---

## 12. 已知技术债与风险

| 项 | 位置 | 说明 |
|---|---|---|
| 超大文件 | `admin.py` 2157 行、`ai_chat_service.py` 1218 行、`Admin.vue` 1599 行、`ResumeEditor.vue` 1527 行 | 与「路由不堆业务、视图不堆表单」的自我约束有张力；`ai_chat_service.py` 同时管会话、上下文、模型选择、校验、合并、决策，改动易牵一发动全身 |
| settings 未固定 | `ai-service/src/agent.ts` | 两处 `createAgentSession` 都未传 `settingsManager`，会读 `<cwd>/.pi-agent/settings.json`，导致 compaction/retry 行为受部署目录影响、不可复现。建议改 `SettingsManager.inMemory({ compaction, retry })` |
| thinking 逐 token 穿两跳 | `agent.ts` → NDJSON → FastAPI → 浏览器 | 每次 1–3 字符，一次响应上百条，每条都要一次 `JSON.parse` + `res.write` + 前端状态更新。建议 Node 侧 80–120ms 节流合并；同时 `res.write` 未处理背压（未监听 `drain`） |
| 工具回调往返 | 每个工具一次跨进程 HTTP | 一轮可能 3 次往返。优化方向：上下文里预置 section 只读快照，让 `get_resume_context` 本地返回 |
| 模板双份实现 | `frontend/src/components/templates/{Classic,Modern,Tech}Template.vue` | 在 `src` 内已无引用，真正生效的是后端 Jinja2 模板；保留会误导且样式会漂移 |
| 超时默认值不一致 | `config.ts` 默认 60000ms，`docker-compose.yml` 传 300000ms | 不走 Docker 直接 `npm run dev` 时，导入整份简历（实测 60s+）会撞默认超时 |
| 计费收尾仍阻塞 | `server.ts` 在 `res.end()` 后 `await reportUsage` | 注释写 fire-and-forget，实际占住请求收尾；FastAPI 慢时会拖长 Node 连接生命周期 |
| 文档过时 | `AGENTS.md` 仍写 LangChain/LangGraph | 旧管线已退役，应按 `docs/pi-agent重构设计.md` 理解 |
| 静态共享令牌 | `AI_SERVICE_TOKEN` | ADR 中已列为开放问题。当前「内网 + 无 shell 工具 + Python 侧兜底校验」下风险可控；升级方向是短时签名（时间戳 + HMAC） |

---

## 13. 术语表

| 术语 | 含义 |
|---|---|
| 模型目录（catalog） | `ai-service/.pi-agent/models.json`，定义 provider/model 的能力与 Key 引用 |
| 模型策略（model config） | MySQL `ai_model_configs` 一行，业务侧「启用哪个模型、多少钱、带几条上下文」 |
| 模型引用（model_ref） | 策略指向目录中的具体 `provider/model` 的字符串 |
| 待确认变更（pending change） | AI 产出但未应用的简历修改，挂在 assistant 消息上，需用户确认 |
| 起手式（resume starter） | 按行业预置的简历骨架与示例内容，新建时可选 |
| 积分（flow points） | 平台内计费单位，AI 调用与部分功能消耗 |
| 回合（turn） | 一次用户输入到 Agent 结束（`agent_settled`）的完整过程 |
| phase | 流式事件，用于提示「正在读取简历 / 正在改写 / 正在校验」等阶段 |
| section | 简历的模块单元（education、work、projects…），每条目有稳定 id |
| template_config | 模板的配色变量（theme_color、bg_color、icon_color），存于简历 |

---

## 14. ADR 索引

| 编号 | 主题 |
|---|---|
| 0001 | 为什么保留 Python 后端、把 AI 拆成 Node sidecar |
| 0002 | 为什么用统一对话入口替代 9 个独立功能 API（简历评分功能被移除） |
| 0003 | 为什么 MySQL 是会话事实源、Agent 会话请求级重建 |
| 0004 | 为什么用 section 级工具 + 回调 Python 校验（替代整份重发） |
| 0005 | 为什么模型配置事实源迁到 AI 服务的模型目录 |

详细端到端链路（含行号）见 `docs/ai-call-flows.md`；重构动机与取舍见 `docs/pi-agent重构设计.md`。
