# ResumeFlow AI 服务

承载 pi-agent 运行时的 Node.js 旁路服务。FastAPI 通过 HTTP/SSE 调用它，并保留认证、数据库、计费（见 `docs/architecture/pi-agent重构设计.md`）。

## 当前状态（Ticket 01 骨架）

只打通「纯对话回复」的读链路：

- 独立进程，接受 `POST /v1/chat`，返回 NDJSON 事件流（`start` / `phase` / `delta` / `result` / `error`），与前端既有流式契约一致。
- **默认 Faux Provider 模式**：无网络、确定性输出，测试与演示不依赖真实模型，非确定性被隔离。
- 会话为请求级 `SessionManager.inMemory()`，用后即弃，无状态可水平扩展。
- **零工具白名单**：`tools: []`，不暴露任何修改路径（纯只读）。
- 覆盖 pi 默认编码助手人设，改为简历助手系统提示词。
- 服务间鉴权：静态令牌（`Authorization: Bearer <token>`）。
- **真实 Provider（ticket 07）**：`PI_PROVIDER=real` 时从 `.pi-agent/models.json` 加载模型（openai-completions），见下文「真实模型接入」。

## 对话快捷指令（/润色 · /翻译 · /优化 · /生成）

四个高频操作做成对话内快捷指令。它们以 pi-agent PromptTemplate 注册，`/润色 工作经历`、`/优化 <JD>` 等在 `session.prompt` 时自动展开（`$@` 代入参数），模型随后走与自然语言相同的工具/校验链路：`get_resume_context` → `update_section`/`propose_change` → FastAPI 数值校验 → 待确认修改。详见 `docs/本地开发环境启动指南.md` 的「对话内快捷指令」表。

## 多模态图片输入（/上传图片）

求职者上传简历图片/截图后，FastAPI 代理把 MinIO/OSS 对象解析成 **base64** 的 `ImageContent[]`，随 `images` 字段传给本服务；`server.ts` 解析后经 `session.prompt(text, { images })` 原生附加给模型，模型据此走**相同的** section 工具与待确认链路（不另建管线）。

- **图片必须是 base64**：pi 的 provider 把 `ImageContent.data` 当作裸 base64，自己拼 `data:<mime>;base64,<data>` 的 data-URL（openai-completions 等）。所以代理在 FastAPI 侧读对象字节→base64，本服务不做任何存储读取。
- **模型需声明 image 输入**：provider 只有 `Model.input` 含 `"image"` 时才转发图片块。真实 vision 模型（如 deepseek-v4-flash-vision-exp）的 `models.json` 须写 `"input": ["text","image"]`；否则图片被静默丢弃。Faux 模型默认 `["text","image"]`。
- **多模态开关**：FastAPI 代理在 `supports_multimodal=false` 时直接拒绝图片（报错而非静默丢弃），返回与旧 LangChain 链路一致的提示。纯文本请求不带 `images` 键，行为不受影响。
- **实测结论**：图片→改动走同一工具/校验链，Faux 演示用确定性序列（`get_resume_context` → `propose_change`）验证代码路径。**真实模型（deepseek-v4-flash-vision-exp）对图片的识别与多步调用需 Docker MySQL + API Key 联调（环境阻断）**；受 image 输入声明与 base64 data-URL 两项约束，理论兼容、待实测。

## 真实模型接入（PI_PROVIDER=real）

`PI_PROVIDER=real` 时启动走 `ModelRuntime.create({ modelsPath: ".pi-agent/models.json" })`，从 models.json 加载自定义模型（当前用 `openai-completions` API 接入 DeepSeek 兼容接口）。

**配置步骤**：

```bash
cp models.json.example .pi-agent/models.json   # 按需修改模型列表
# 在 .env 里设置 DEEPSEEK_API_KEY（models.json 以 $DEEPSEEK_API_KEY 引用，密钥不落盘）
```

- **models.json 结构**：顶层 `{"providers": {"<provider-id>": {...}}}`；provider 支持 `baseUrl` / `apiKey` / `api` / `models[]`，模型项含 `contextWindow` / `maxTokens` / `input`（vision 模型须含 `"image"`）/ `compat`（DeepSeek 兼容覆盖：`supportsDeveloperRole: false`、`supportsStore: false`、`maxTokensField: "max_tokens"`、`supportsReasoningEffort: false`）。
- **API Key**：只走 `$ENV` 环境变量展开，不写进 models.json 本体；`DEEPSEEK_API_KEY` 缺失时该模型不会出现在可用列表。
- **模型选择**：请求体带 `model` 字段（取自 `AiModelConfig.pi_model_ref`，格式 `provider/model`，如 `deepseek/deepseek-v4-flash-vision-exp`）时按引用精确选模型；不带时取 models.json 中第一个已配置认证的模型。引用格式错误或模型未配置 → 直接回 `error` 事件。
- **faux 回退**：`PI_PROVIDER=faux`（默认）完全不依赖网络与密钥，行为不变。

## 按模型计费入账（token 用量回调）

每轮对话从 pi 事件流归集 token 用量（`message_end` 上的 assistant `usage`，agent 每轮工具循环各计一次输入 token，求和即 provider 实际口径），按 `provider/model` 分桶，**成功结束的轮次**（出错轮不回传、不计费）在响应流结束后 POST 回 FastAPI：

```
POST {PI_FASTAPI_BASE_URL}/api/ai/service/usage     # 头：x-ai-service-token
{ "user_id": 7, "resume_id": 3, "usage": [{ "provider": "deepseek", "model": "...", "requests": 2, "input_tokens": 160, "output_tokens": 50, "cache_read_tokens": 10, "cache_write_tokens": 16, "total_tokens": 236 }] }
```

FastAPI 侧按 `pi_model_ref` 反查 `AiModelConfig` 取该模型的 chat 计费率（`billing_override`），走与旧 LangChain 链路**同一条** task + `settle_task_flow_points` 账本路径入账（对账口径一致）；未映射的 ref 按默认 ai_chat 规则兜底计费，不丢 charge。

## 模型目录管理台（参考 magpie）

`.pi-agent/models.json` 仍是唯一事实来源；管理台（AI 设置 → 模型目录）通过 FastAPI 代理维护它，能力对标 magpie 的供应商卡片：

| 功能 | 端点 | 说明 |
|------|------|------|
| 获取上游模型 | `POST /v1/catalog/remote-models`（卡片里填的地址/密钥，空值回落到 `provider_key` 已存的那份）· `GET /v1/catalog/providers/:key/remote-models`（按已存配置） | 拉上游 `/v1/models`，并用 models.dev 补上 `context_window` / `max_tokens` / `input`（资料库不可用时只返回 id）；勾选后随服务商一起提交，**不需要先保存服务商** |
| 保存服务商 | `POST/PUT /v1/catalog/providers`（body 可带 `models { added, removed, patches }`） | 服务商与模型增删改在同一次 `mutateCatalog` 落盘，不会出现「服务商建好了模型没进来」 |
| 补全模型资料 | `POST /v1/catalog/providers/:key/sync-dev` | 从 models.dev 填充上下文窗口/最大输出/多模态/价格（**只填空白，不覆盖手填值**）；服务商标识用 models.dev 官方 id（如 `deepseek`）即可匹配 |
| 测试连通性 | `POST /v1/catalog/providers/:key/probe` | 16-token 最小请求，返回时延或错误 |
| 查余额 | `GET /v1/catalog/providers/:key/balance` | DeepSeek/Moonshot/OpenRouter/智谱/硕方/阶跃等内置适配（60s 缓存，失败回落上次读数） |
| 查套餐用量 | `GET /v1/catalog/providers/:key/plan-usage` | 套餐型厂商（火山方舟 Coding/Agent Plan、阿里/腾讯 Token Plan）按窗口展示已用百分比；需要账号级 AK/SK（在服务商表单里填，不随推理请求发送） |

套餐识别来自接口地址特征（`ark.*.volces.com/api/coding|plan`、`token-plan.*.maas.aliyuncs.com`、`tencentmaas.com/plan`），无需额外开关。三家签名实现（火山 V4、阿里 ACS3、腾讯 TC3）有 magpie 同源的固定测试向量兜底。

## 路由组（选型期静默 failover）

多个目录模型可以绑成一个 `group/<id>` 引用（管理台「路由组」板块维护，落在 models.json 的 `routing_groups`）。模型策略绑定组引用后：

- **选型期 failover**：每次对话开始前从组内挑一个可用成员；失败过的成员进入 5 分钟冷却，后续轮次自动跳过。刻意不做回合中途换上游——流式事件已发出，重试会重复可见内容。
- **会话粘性**：同一会话 30 分钟内固定用同一个成员，避免切换上游浪费厂商的提示词缓存。
- **策略**：`order`（按列表顺序）、`rotate`（不同会话轮换）、`usage`（优先用得最少的）。
- **计费**：成员模型在后台没有单独映射时，按组引用对应的模型策略结算，组内扣费规则保持一致。
- **能力**：组的多模态 = 成员交集（任一成员不支持图片即不支持）；上下文窗口取成员最小值。

组被模型策略引用时禁止删除（409）；成员模型被删除后需回编辑组剔除。

## 运行

```bash
cd ai-service
npm install
cp .env.example .env      # 设置 AI_SERVICE_TOKEN，与 backend/.env 的 AI_SERVICE_TOKEN 一致
npm run dev               # tsx watch 启动，端口 8300
```

验证：

```bash
curl http://127.0.0.1:8300/health
curl -N -X POST http://127.0.0.1:8300/v1/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"message":"这份简历怎么样？"}'
```

## 版本基线

`@earendil-works/pi-coding-agent@0.83.0`（锁版，不装 latest），`@earendil-works/pi-ai@0.83.0`。升级前按 `../.claude/skills/dg-piagent/SKILL.md` 的版本协议评估并同步更新 skill。

## Docker

ai-service 不属于任何 profile，默认 `docker compose up -d` 就会随基础设施一起启动：

```bash
docker compose up -d ai-service
# 或随基础设施一起
docker compose up -d
```

需要连同后端与前端一起全栈启动时使用 `full` profile：

```bash
docker compose --profile full up -d --build
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `npm run dev` | 热重载启动（tsx watch） |
| `npm start` | 一次性启动（tsx） |
| `npm run typecheck` | TS 类型检查 |
