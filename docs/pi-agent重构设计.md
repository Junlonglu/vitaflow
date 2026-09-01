# VitaFlow AI 模块重构设计：pi-agent 化

> status: accepted · 2026-09-01
> 决策记录见 `docs/adr/0001~0004`，术语表见 `CONTEXT.md`
> 本文是纸面设计的最终产物，编码后置；同时作为旧链路退役后的回滚依据。

## 1. 背景与动机

实测（`docs/learning/05-AI功能链路分析与耗时排查.md`）：对话改简历中「广州→深圳」这类微小修改因整份简历 JSON 重传耗时 30~50s，逼近 `AI_TIMEOUT=60` 超时线。

重构三重动机（按优先级）：

1. **学习 pi-agent SDK**：用真实项目掌握 Agent 框架二次开发
2. **解决延迟**：Agent + 工具做增量修改，目标「改地点」~5s
3. **获得 Agent 架构能力**：事件驱动、工具系统、统一会话

## 2. 目标架构

```
浏览器（AiChat 组件，唯一 AI 界面）
   │  HTTP/SSE（接口路径基本不变）
   ▼
FastAPI（Python，既有职责全部保留）
   │  - 认证 / JWT / 用户 / 简历 CRUD
   │  - MySQL（唯一事实源）、Redis、MinIO
   │  - Flow Points 计费入账
   │  - 数值校验 + 待确认变更落库
   │  - PDF/Word 导出（Playwright / WeasyPrint，不动）
   │  │
   │  │ HTTP + SSE 代理（服务令牌认证）
   ▼  ▼
Node「AI 服务」（新增，pi-agent 运行时）
   │  - createAgentSession（请求级，用后即弃）
   │  - 自定义工具：get_resume_context / update_section / propose_change
   │  - 工具回调 FastAPI 完成校验落库
   │  - token 用量归集 → 回调计费
   │  - /润色 /翻译 /优化 /生成 快捷指令（PromptTemplate）
   │  │
   ▼  ▼
LLM Providers（models.json 配置：deepseek-v4-flash-vision-exp（百万上下文）、glm 等，OpenAI 兼容）
```

### 职责划分

| 职责 | 归属 | 说明 |
|------|------|------|
| 认证、用户、简历数据 | FastAPI | 不变 |
| 会话/消息/待确认变更持久化 | FastAPI + MySQL | 唯一事实源（ADR-0003） |
| Agent 循环、工具编排、流式事件 | Node AI 服务 | pi-agent 核心三件套 |
| 数值防幻觉、待确认人审 | FastAPI（工具回调端点） | ADR-0004 |
| token 归集与计费 | Node 归集 → FastAPI 入账 | 模型粒度延续 |
| PDF/Word 导出 | FastAPI | 与 AI 重构无关 |

## 3. Agent 会话设计

- **请求级会话**：每次对话请求 `createAgentSession`（`SessionManager.inMemory()`），请求结束 `dispose()`。无状态、可水平扩展。
- **上下文注入**：FastAPI 按模型的 `context_messages` 上限从 MySQL 取历史消息 + 简历快照 + pending_change 摘要，随请求体传给 AI 服务（pi 场景 G01/G04 的注入模式）。
- **系统提示词**：必须覆盖 pi 默认编码助手人设（场景 A03），定义简历助手角色 + 输出语言规范（沿用现有 prompts.py 的中文规范条款）。
- **工具白名单**：禁用 `bash/edit/write/read` 编码四件套（场景 A04），仅注册下述业务工具。

## 4. 工具集（section 级，ADR-0004）

| 工具 | 参数 | 行为 |
|------|------|------|
| `get_resume_context` | `sections?: string[]` | 读取当前简历指定/全部 section（回调 FastAPI 或启动时注入） |
| `update_basics_field` | `field, value` | 字段级更新基本信息（如 location）→ 回调校验落库 |
| `update_section` | `section, items` | section 级更新，以 `id` 定位条目，未提及条目不触碰 → 回调校验落库 |
| `propose_change` | `summary, sections` | 创建待确认变更（pending_change），不直接写入 |

回调链路（工具 → FastAPI 内部端点）执行：

1. **数值防幻觉**：提案中量化结果必须来自简历或对话历史（`_unverified_numeric_claims` 语义重实现）
2. **写入待确认变更**：保持「用户点应用才生效」的产品语义
3. 返回工具结果（成功/被拦截原因），Agent 据此继续对话

延迟收益：单次工具调用输出从 ~10KB 降至 KB 级。

## 5. 快捷指令（PromptTemplate）

| 指令 | 承接的旧功能 | 形态 |
|------|-------------|------|
| `/润色` | optimizeSection | 指令模板 + 参数（模块名） |
| `/翻译` | translateResume | 指令模板（zh↔en，结构保护） |
| `/优化` | optimizeByJd（原 LangGraph 8 节点） | 指令模板 + JD 参数，Agent 多步工具调用 |
| `/生成` | generateResume | 指令模板（可基于行业模板） |

未选中的能力（图片导入、对话图片）靠多模态自然对话触发，不设指令。评分功能退役（ADR-0002）。

## 6. 事件流与计费

- **流式**：pi `session.subscribe` 事件（`message_update` 的 text_delta 等）→ Node 侧 SSE → FastAPI 纯代理转发 → 前端（接口路径不变，ADR-0001）。
- **结束信号**：订阅 `agent_settled`（v0.83.0 推荐）判定本轮真正结束。
- **token 归集**：从事件流的 usage 信息按模型归集，请求结束时回调 FastAPI 计费端点，沿用 `ai_model_configs` 的模型粒度单价（points_per_call + 百万 token）。
- **注意**：订阅层收不到扩展独有事件（`tool_call`/`tool_result` 等 6 个）；若需这些事件的数据，必须写成扩展走 `pi.on`，且回调落库 fire-and-forget，避免阻塞 agent loop。

## 7. Provider 接入

**结论：pi-agent 可直接接入 OpenAI 兼容服务，两条路径。首版用路径①（零代码），热切换走路径②。**

### 7.1 路径①：models.json 静态配置（首版）

文件放在 `PI_CODING_AGENT_DIR` 指向的目录下的 `models.json`（或通过 `modelsPath` 指定）。接入当前 deepseek 模型 + 备用 glm 的完整示例：

```json
{
  "provider": [{
    "name": "deepseek",
    "baseUrl": "https://api.deepseek.com",
    "apiKey": "$DEEPSEEK_API_KEY",              // 支持 $ENV / ${ENV} / !command / 字面值
    "api": "openai-completions",
    "models": [
      {
        "id": "deepseek-v4-flash-vision-exp",
        "name": "DeepSeek V4 Flash Vision",
        "reasoning": true,
        "contextWindow": 1000000,               // 百万上下文，pi 靠它自动压缩（必须写准）
        "maxTokens": 8192,                       // 输出上限，等价于现 .env 的 AI_MAX_TOKENS
        "input": 0, "cost": 0,                   // 计费在 FastAPI 侧，跨模型归集
        "compat": {
          "supportsDeveloperRole": false,
          "supportsStore": false,
          "maxTokensField": "max_tokens",
          "supportsReasoningEffort": false
        }
      },
      { "id": "glm-5.1", "name": "GLM-5.1", "contextWindow": 128000 }
    ]
  }]
}
```

要点：

- `api: "openai-completions"` 声明协议，内置 SSE 流处理，无需自写 `streamSimple`
- **`contextWindow` 千万写准**：pi 依赖它做 auto-compaction；写错的话，模型超窗时报错文案 pi 不认识，需要 `message_end` handler 改写为 `context_length_exceeded` 才能触发压缩
- 传 `models` 是**替换**不是追加；同一 provider 的多个模型直接列在数组里
- 自定义 provider 用独立名字，别覆盖内置的 `anthropic` / `openai`（会清空内置模型）

### 7.2 兼容性注意

- **非 OpenAI 原生服务（DeepSeek/智谱/千帆）几乎都需要 `compat` 覆盖**：`supportsDeveloperRole: false`、`supportsStore: false`、`maxTokensField: "max_tokens"`、`supportsReasoningEffort: false`，否则服务端会拒收参数（如 `store`、`reasoning_effort`）
- 本模型带 `vision`，为多模态——需验证 pi 侧图片输入（base64 / URL）的兼容性（涉及「图片导入简历」功能）
- apiKey 含 `$`/`!` 需转义为 `$$` / `$!`；`openai-completions` 下 OpenAI SDK 自动带 Bearer，不需要 `authHeader: true`，401 通常是 apiKey 没解析到
- 模型 config 里的 `timeout` 继续沿用现 `.env` 的 `AI_TIMEOUT=60`

### 7.3 路径②：扩展 `pi.registerProvider`（后续热切换）

管理后台需要动态增删模型时引入。签名概貌：

```ts
pi.registerProvider(name, {
  baseUrl, apiKey,        // apiKey 支持 $ENV 插值
  api: "openai-completions",
  headers,                // 自定义 header 签名
  models,                 // 动态模型列表
  oauth, streamSimple,
})
```

适合需要动态模型列表、OAuth、自定义签名头的场景；与「模型粒度计费 + 管理后台配置」对接。首版不引入，避免 M1 复杂度。

## 8. 数据模型变化

| 表 | 变化 |
|----|------|
| `ai_chat_sessions` / `ai_chat_messages` | 保留，仍为事实源 |
| `ai_tasks` / 评分相关字段 | 评分退役，迁移时清理 |
| `ai_model_configs` | 保留；增加 pi 侧 model id 映射字段（如 `pi_model_ref`） |
| `flow_point_transactions` | 保留，入账来源改为 AI 服务回调 |

## 9. 部署

- docker-compose 新增 `ai-service`：Node 22 镜像，安装 `@earendil-works/pi-coding-agent@0.83.0`（**锁版本，不装 latest**——skill 基线协议），`PI_CODING_AGENT_DIR` 指向容器内配置目录挂载 models.json。
- FastAPI 新增环境变量：`AI_SERVICE_URL`、`AI_SERVICE_TOKEN`。
- 服务间认证：内网 + 静态令牌 header。

## 10. 退役清单与回滚

**删除范围**（迁移验证通过后）：`services/ai/chains.py`、`graphs.py`、`prompts.py`、`schemas.py`、`llm.py`、`token_usage.py`，requirements 中 `langchain`/`langchain-openai`/`langgraph`，前端评分组件与 API。

**回滚依据**：本设计文档 + `docs/learning/` 四篇分析 + git 历史。

## 11. 实施里程碑

1. **M1 POC**：独立目录跑通「改地点」——Agent + update_basics_field 工具 + 回调校验，实测延迟对比
2. **M2 服务化**：Node AI 服务 + FastAPI 代理 + SSE 贯通 + 计费回调
3. **M3 功能收敛**：四快捷指令 + 图片多模态 + 前端统一到 AiChat
4. **M4 退役**：删除旧链路代码，文档归档

## 12. 风险与开放问题

- [ ] 目标模型（deepseek-v4-flash-vision-exp）对 tool calls 的兼容性（function calling 支持质量）需在 M1 实测
- [ ] 工具回调 FastAPI 的内部端点鉴权（服务令牌 vs 内部签名）
- [ ] Agent 多步循环的最坏情况延迟（`/优化` 场景）是否反而高于固定管线
- [ ] pi 事件流中 usage 归集的准确性需与旧 `token_usage.py` 对账
