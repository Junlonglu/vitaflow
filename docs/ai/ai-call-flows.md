# AI 能力调用全流程

> 面向开发者的端到端参考。所有路径以仓库根为基准；行号为 2026-09-04 的代码状态，后续可能漂移，以符号名为准。
>
> 架构原则（ADR-0003 / ADR-0005）：**ai-service（Node 旁路，:8300）是唯一 AI 事实源**——持有模型目录、跑 agent 循环、产出流式事件；backend（FastAPI，:8000）拥有鉴权、数据库、计费；前端从不直连 ai-service，一律经 backend 代理。

## 0. 分层总览

```
前端 (Vue3, :5173)
  │  fetch + ReadableStream 读 NDJSON（ai.ts postAiStream）
  ▼
backend (FastAPI, :8000)                      ai-service (Express+pi-agent, :8300)
  ├─ 鉴权 / 积分预检 / 任务落库         ──▶    ├─ /v1/chat   对话（流式 NDJSON）
  ├─ 转发请求 ai_service_client.py     ──▶    ├─ /v1/task   一次性任务（生成/导入）
  ├─ 工具回调校验端点 /service/tool/*   ◀──    ├─ /v1/catalog 模型目录 CRUD
  ├─ 计费上报端点 /ai/service/usage    ◀──     └─ /health     存活探针
  └─ 结算 Flow Points（MySQL + Redis）
```

服务间鉴权：静态令牌双向校验。backend 侧 `settings.ai_service_token`（backend/.env `AI_SERVICE_TOKEN`）；ai-service 侧 `src/config.ts` 读取同名环境变量；路由内 `require_ai_service_token`（backend/app/api/ai.py:123）与 ai-service 的 `authorize()`（src/server.ts:71）各自把关，缺失时一律 401（fail-closed）。

## 1. 配置与目录层

| 数据 | 存放位置 | 维护方式 | 说明 |
|---|---|---|---|
| 模型目录（provider/baseUrl/apiKey/模型列表） | `ai-service/.pi-agent/models.json`（volume 挂载） | 管理后台「服务商接入」经 backend 代理 CRUD | 纯 UI CRUD：frontend `AiCatalogPanel.vue`（列表容器 + 一次只开一张的展开控制）/ `AiProviderCard.vue`（摘要行 + 展开成表单）→ backend `admin/ai_configs.py` 的 `/admin/ai-catalog/*` → `call_catalog`（ai_service_client.py:255）→ ai-service `/v1/catalog*`（server.ts:133-256），写后 `rebuildRuntime()` 热生效，无需重启；一次保存只发一条写请求：provider 的 POST/PUT 带 `models { added, removed, patches }`，ai-service 在同一个 `mutateCatalog` 里改资料→加新 id→删取消勾选的（§6.27）；拉上游列表走 `POST /v1/catalog/remote-models`，用卡片里填的地址与密钥，不要求先保存 |
| 模型策略（哪个 ref 启用、上下文条数、按模型定价） | MySQL `ai_model_configs` 表 | 管理后台「模型策略」 | `AiModelConfig`（backend/app/models/ai_config.py），运行时快照由 `get_active_ai_config`（ai_config_service.py:189）读取，Redis 缓存 |
| 计费规则（每功能兜底价） | MySQL `flow_point_rules` 表 | 管理后台「积分规则」 | 兜底价：生成 100 点/次、导入 50 点/次、对话 5 点/次+百万 token 费率（`DEFAULT_POINT_RULES`，flow_points_service.py:35） |
| Provider 模式 | 环境变量 `PI_PROVIDER`（faux/real） | 根目录 .env → docker compose 注入 | faux=本地确定性演示模型；real=从 models.json 加载真实模型（real.ts） |

定价优先级：**模型级定价（`ai_model_configs.chat_points_per_*`）逐键覆盖 `flow_point_rules`**，逻辑在 `calculate_points` 的 `pricing_override` 参数（flow_points_service.py:203-224），override 数据来自 `AiChatModelConfig.chat_billing_override()`（ai_config_service.py:44）。

## 2. 对话流程（AI 助手）

### 2.1 前端

1. 入口组件 `ResumeAiChatPanel.vue` 是纯展示组件，只 emit `send/regenerate/confirm/reject/clear`；真正的调用在父级 `ResumeEditor.vue`（sendChatMessage :553、regenerateChatMessage :655）。
2. API 层 `frontend/src/api/ai.ts`：
   - `sendResumeChatMessageStreamApi`（:180）→ `POST /api/ai/resume-chat/{id}/messages/stream`
   - `regenerateResumeChatMessageStreamApi`（:182）→ `.../messages/{message_id}/regenerate/stream`
   - 两者都走 `postAiStream`（:87-177）：**fetch + ReadableStream 逐行读 NDJSON**（非 EventSource），事件类型 `start / delta / phase / result / error`。
3. 图片附件先经 `uploadAiChatImageApi`（api/file.ts:15）→ `POST /files/upload-ai-chat-image` 存 MinIO，对话请求只带附件引用。

### 2.2 backend（backend/app/api/ai.py + services/ai_chat_service.py）

1. 路由 `resume_chat_send_stream`（ai.py:527）→ `stream_ai_service_chat`（ai_chat_service.py:301）：
   - `get_or_create_chat_session`（:86）建/取会话，用户消息落库（`ai_chat_messages` 表）
   - `build_ai_service_context`（:151）拼装简历上下文摘要（`_resume_intent_summary`）
   - `_resolve_turn_model_config`（:198）选本轮模型（用户可从「助手可选」策略中挑，缺省用启用策略）
   - `_reject_turn_images`（:218）多模态守卫：模型目录标记不含 image 的策略收到图片直接拒
   - `_precheck_turn_points`（:228）按 points_per_call 预检余额（402 拒绝）
2. `ai_service_chat_events`（ai_service_client.py:73）组装 payload（message/system_prompt/model_ref/user_id/resume_id/images base64）POST ai-service `/v1/chat`，流式转发 NDJSON。
3. 流结束时保存助手消息、生成 `AiChatMessage`（含 propose-change 时带 `optimized_resume_data` 与 pending 状态）。

### 2.3 ai-service（src/）

1. `/v1/chat`（server.ts:276）校验入参（message 必填、images 解析为 `ImageContent`、model_ref、context），设置 NDJSON 响应头后调 `runChat`（agent.ts:132）。
2. `runChat`：
   - 解析 runtime：faux（src/faux.ts，确定性演示）或 real（src/real.ts `resolveRealModel`：按 `provider/model` 从 models.json 取模型）
   - system prompt：请求自带 `system_prompt`，否则 `buildContextPrompt(DEFAULT_SYSTEM_PROMPT, context)`（src/prompt.ts）
   - pi-agent 单轮推理，流式产出事件（`emit` 回调 → server.ts `sendEvent` 写 NDJSON 行）
   - 返回按模型聚合的 `ModelUsage[]`
3. **工具回调**：唯一的写工具 `update_resume`（src/tools/resume.ts）不直接改数据，而是把 `changes[]` / `from_library[]` 归一成 backend 的 `updates` 形状，回调 `/api/ai/service/tool/update-resume`（`PI_FASTAPI_BASE_URL`）。该端点先做信息库导入，再按 id 合并模块，最后跑一次数值守卫（`validate_propose_change`），只回待确认快照不落库。另有只读的 `search_library`（`/info-fragments/search`）、写信息库提案的 `save_library`（`/info-fragments/propose`），以及本地的 `ask_user` / `use_skill`。
4. **回合结束计费**：`/v1/chat` 里 `runChat` 返回后，过滤 faux usage，`reportUsage`（src/billing.ts:22）带服务令牌 POST backend `/api/ai/service/usage`（server.ts:305-316，fire-and-forget，不影响用户已收到的回复）。

### 2.4 异步计费入账（ai.py:729-806）

`/ai/service/usage` 收到按 `provider/model` 逐条的真实 usage 后：
- `get_ai_config_by_model_ref` 找到策略（找不到则用 ai_chat 兜底规则）
- 每条 usage **新建一条 ai_chat 类型 AiTask** 并 `finish_ai_task("success")`
- `settle_task_flow_points`（flow_points_service.py:340）按模型级定价入账；若结算失败（如 402）任务落 failed 并退还预扣

## 3. 一次性任务流程

### 3.1 AI 生成简历（generate_resume，流式）

1. 前端：`ResumeCreateFlow.vue` 的 AI 生成弹窗（工作区根部挂载，AppDialog 外壳）→ `generateResumeStreamApi`（ai.ts:179）→ `POST /api/ai/generate-resume/stream`。
2. backend：路由 `generate_resume_stream_api`（ai.py:505）→ `new_task`（ai.py:151）：
   - `estimate_tokens` 预估输入 token（token_usage.py:34，中文≈1字/token、非中文≈4字符/token 的保守估算）→ `precheck_flow_points` → `consume_flow_points` **立即预扣**
   - 创建 AiTask（pending），`input_data.model` 记目录模型 ref
3. `_ai_service_task_stream_response`（ai.py:260）→ `ai_service_task_events`（ai_service_client.py:151）POST `/v1/task`（task=generate_resume），逐事件转发给前端。
4. ai-service：`runTask`（agent.ts:330）按 `taskPrompts.ts` 预置的生成 prompt 推理，产出 `result` 事件（含解析后的 `resume_data` JSON + 聚合 usage）。**注意：/v1/task 不做 reportUsage 回调**（backend 同步结算，避免 task_type 硬编码）。
5. backend 在 `result` 事件到达时：校验 `resume_data` → `normalize_resume_data` 归一化 → `build_token_usage(..., estimated=False)` 用真实 usage → `finish_ai_task("success")` → `settle_task_flow_points` **多退少补**；流断开/异常则 `finish_ai_task("failed")` 走退款（ai_task_service.py:70 `_refund_failed_task_points`：汇总该任务流水净额为负则回补余额并写"AI 任务失败，自动退还"正数流水）。

### 3.2 导入简历（import_resume，一次性请求）

1. 前端：`ResumeCreateFlow.vue` → `importResumeFileApi`（api/resume.ts:44，FormData：files + template_id）→ `POST /api/resumes/import-file`。
2. backend（resumes.py:102）：
   - **本地抽文本**（resume_import_service.py:100）：PDF 走 pypdf（:19）、docx 走 python-docx（:32，段落+表格）、TXT/MD 直接 decode；图片走 pytesseract OCR（:51，需本机 Tesseract）。清洗后 <20 字符拒绝，截断 30000 字符
   - `detect_resume_language`（resume_locale.py:87）判语言 → `estimate_tokens` → `precheck_flow_points` → 建 AiTask → 预扣
   - `ai_service_task_result_sync`（ai_service_client.py:233，`asyncio.run` 同步桥）一次性等待 ai-service 返回 `(result, usage)`
3. ai-service：同 `/v1/task`（task=import_resume），模型解析文本输出结构化简历 JSON；单轮受 `AI_TURN_TIMEOUT_MS` 约束（根 .env 现为 300000ms——真实模型解析整份简历 60s 不够）。
4. backend 收到结果：`normalize_resume_data` → `create_resume` 建简历 → `settle_task_flow_points` 结算 → 返回简历 id，前端跳转编辑器。

## 4. 模型目录与多模态能力

- 目录即 models.json；结构示例 `ai-service/models.json.example`。apiKey 支持 `$ENV_VAR` 引用（如 `$DEEPSEEK_API_KEY`，密钥不落盘）；管理后台的服务商卡提供环境变量名下拉（ai-service `GET /v1/catalog/env-keys`，server.ts:147，只返回 `*_API_KEY` 变量**名**）。
- 接入界面是**摘要行 + 展开成表单**（`AiProviderCard.vue` + `providerDraft.ts`）：列表里每行只有名称/标识/域名 · 子项数 · 前三个子项名 + 余额或用量，点开才出现身份/密钥/端点/模型芯片的表单，一次只开一张；所有改动先落草稿，点保存才把服务商与 `models { added, removed, patches }` 合成**一条**写请求，取消或收起即整体回滚（收起前有未保存改动会先确认，§6.28）。不再有新增/编辑/模型/拉取四个弹窗，也不再让每条记录都摊开。
- 模型 `input` 数组含 `image` 即多模态：前端目录/策略面板显示「多模态」徽标；对话链路用它做图片守卫（2.2 第 4 步）。
- backend 侧能力推导用 `afetch_catalog_providers`（ai_service_client.py:270，异步拉目录）。

## 5. 可观测性与统计

- 每次调用都落 `ai_tasks` 表：`task_type / status / tokens_used / input_data（含 model ref、真实 usage）/ points_used / model_name（展示快照）`。
- 管理后台仪表盘「模型调用统计」**按 `input_data.model`（ref）聚合**（admin.py:597-610），与按模型计费同口径；历史裸 id 能唯一映射到已配置 ref 时自动归并。
- 计费流水：`flow_point_transactions` 表（`add_point_transaction`，flow_points_service.py:299）。
- 用户侧：`/ai/records`、`/ai/histories`、`/ai/flow-points(+ /transactions)`（ai.py:362-447）。

## 6. 附：backend/app/services/ai_service_client.py 函数清单

| 函数 | 行号 | 职责 |
|---|---|---|
| `ai_service_chat_events` | :73 | POST `/v1/chat`，流式转发对话事件；组装 context、图片 base64、model_ref |
| `ai_service_task_events` | :151 | POST `/v1/task`，流式转发一次性任务事件 |
| `ai_service_task_result` | :205 | 消费任务事件流至 result/error，返回 `(result_data, usage)` |
| `ai_service_task_result_sync` | :233 | `asyncio.run` 同步桥，供 `def` 路由（import）在线程池调用 |
| `call_catalog` | :255 | 同步目录代理（admin CRUD 用），非 2xx 抛 AppException |
| `afetch_catalog_providers` | :270 | 异步拉目录 providers（多模态能力推导） |

## 7. 附：ai-service HTTP 端点（src/server.ts）

| 端点 | 行号 | 说明 |
|---|---|---|
| `GET /health` | :89 | 存活探针（含 provider 模式） |
| `GET /v1/catalog` | :133 | 目录读取（faux 返回内置 faux/demo） |
| `GET /v1/catalog/env-keys` | :147 | `*_API_KEY` 环境变量名列表（管理 UI 下拉用） |
| `POST/PUT/DELETE /v1/catalog/providers[/:key][/models/:id]` | :154-256 | 目录 CRUD，写后热重建 runtime |
| `POST /v1/chat` | :276 | 对话（NDJSON 流式），结束异步 reportUsage |
| `POST /v1/task` | :342 | 一次性任务 generate_resume / import_resume，usage 随 result 事件返回 |
