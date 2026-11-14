# AI 对话改简历（pending-change）链路详解

> 补充 `ai-call-flows.md` 的深入篇：对话链路中前后端与 ai-service 的数据流、校验与落库时机。
> 行号为 2026-09-04 代码状态，可能漂移，以符号名为准。

## 1. 全链路时序

```
前端 ResumeEditor (sendChatMessage)
  │  fetch + ReadableStream 读 NDJSON（ai.ts postAiStream）
  ▼
backend /api/ai/resume-chat/{id}/messages/stream (ai.py:527)
  │  ① 会话/用户消息落库（先落库，AI 挂了消息也不丢）
  │  ② build_ai_service_context 拼上下文 → ③ 余额预检
  ▼
ai-service POST /v1/chat (server.ts:268)
  │  runChat (agent.ts:132)：选模型 → pi-agent 流式推理
  │
  │  ┌─ 工具回调（模型要改简历时）────────────────┐
  │  │ update_resume（tools/resume.ts）           │ ──POST──▶ backend /service/tool/
  │  │   changes[] + from_library[]               │ ◀──响应── 校验结果（不落库）
  │  │ 校验后 payload 存入 state.pendingChange    │
  │  └───────────────────────────────────────────┘
  ▼
result 事件（回合终点，sendResult agent.ts:181）
  │  result 里携带 toolState.pendingChange（确定性 echo）
  ▼
backend 收到 result（_stream_ai_service_reply, ai_chat_service.py:242）
  │  ④ 助手消息 + optimized_resume_data 落库（pending 状态）
  │  改写 result 事件：pending_change → DB 行（带 message_id）
  ▼
前端渲染确认卡片 → 用户点确认/拒绝
  │  POST /resume-chat/{id}/messages/{message_id}/decision (ai.py:579)
  ▼
backend resolve_chat_change (:939)
  │  ⑤ 纯本地操作：从 DB 读出快照 → 存 ResumeVersion 备份 → 写入 resumes 表
  │  （确认请求不带任何简历数据，不再经过 ai-service）
```

## 2. /v1/chat payload 字段分流

核心区分：**给模型看的** vs **给程序用的**。

| 字段 | AgentInput 键 | 消费点 | 模型可见 |
|---|---|---|---|
| `message` | `message` | `session.prompt()`（agent.ts:296） | ✅ 用户消息 |
| `context` | —（在 server.ts:292 折叠） | 渲染进 systemPrompt 后即丢弃 | ✅（经转写） |
| `system_prompt` | `systemPrompt` | systemPromptOverride（agent.ts:196） | ✅ |
| `images` | `images` | 多模态内容块（agent.ts:295） | ✅（非文本通道） |
| `model` | `modelRef` | `resolveRealModel` 选模型，推理前用掉（:142） | ❌ |
| `user_id`/`resume_id` | `userId`/`resumeId` | 工具 deps，回调 backend 时带回（:161-178） | ❌ |

`context` 生命周期在 server.ts 就结束：`buildContextPrompt` 把它渲染成系统提示词
（简历全文 JSON + 字段清单一行的模块表 + history + pending 摘要）。**简历正文进 prompt**，
因此模型改简历前不需要再读一遍；`get_resume_context` 已从工具白名单移除。

## 3. NDJSON 流与 emit

- NDJSON = 每行一个 JSON 事件，`Content-Type: application/x-ndjson`，普通 HTTP POST 响应
- 事件类型：`start / delta / phase / result / error`
- `emit`（agent.ts:66）是注入的输出回调：`(event) => sendEvent(res, event)`（server.ts:85，
  即 `res.write(JSON.stringify(event) + "\n")`）
- 设计动机：runChat 与 HTTP 解耦（可测、可复用于 /v1/chat 与 /v1/task）

```
pi-agent 事件 → session.subscribe 回调 ──▶ emit ──▶ sendEvent ──▶ NDJSON 行
                                                    │
backend 逐行读（ai_service_client.py:73）── 逐行 yield ──▶ 前端
```

## 4. 数据库写入时刻（backend 独占，ai-service 全程无库）

| # | 时刻 | 写什么 |
|---|---|---|
| ① | 调 ai-service 之前 | `ai_chat_sessions` + `ai_chat_messages`（用户消息） |
| ② | 流式过程中 | **不写**（delta 只累积在内存） |
| ③ | result 事件到达 | `ai_chat_messages`（助手消息 + optimized_resume_data，action_status=pending） |
| ④ | 工具回调期间 | **只读不写**（校验即返回，落库统一推迟到 result） |
| ⑤ | 用户确认/拒绝 | apply：`resumes` 表 + `ResumeVersion` 备份 + 消息状态 applied |
| ⑥ | 异步计费 | `/service/usage`（ai.py:729）→ `ai_tasks` + `flow_point_transactions` |

## 5. toolState 确定性 echo（防模型篡改）

```
模型提议 ──▶ 工具 POST backend 校验 ──▶ 校验后 payload 作为 HTTP 响应发回
                                              │
              toolState.pendingChange ◀──────┘（只有工具函数能写它）
                                              │
result 事件 ────────────── data.pending_change = toolState.pendingChange
```

- 发回 ai-service 的响应 = agent 循环必需的 tool result（推理依赖）
- result 事件带回 = 数据"跟着流走"，零关联成本，backend 无需维护回合状态表
- 关键保证：落库的 JSON 与校验通过的那份**逐字节相同**，模型无法二次加工
  （模型输出只能变成 text 展示内容，没有写 toolState 的通道）

## 6. 简历正文为什么直接进 prompt

早期版本只给模型简历**元信息**（姓名、方向、模块名列表），要看内容必须回调
`get_resume_context`。代价是每轮多一次工具往返、多一份 JSON 输入，模型还常忘记先读。

现在 `build_ai_service_context()` 直接下发 `resume.data`（全文）与 `resume_fields`
（一行一模块的字段清单），于是：

- 省掉一次工具往返，也省掉「模型读了旧快照再改」的时序问题；
- 字段名仍然只有 Python 一份（`schema_compact()` 从 `SECTIONS` 生成），模型不自造字段；
- 代价是每轮多带一份简历 JSON——比起原始的整份 `schema_prompt()`（约 1500 tokens）仍然更省。

## 7. backend 校验流水线（update-resume 端点）

```
提案 {section, proposed}
  │
  ▼
┌─ 关卡 0：结构白名单 ────────────────────────────────┐
│ section ∈ 9 个已知模块；提案有实际内容（非空 dict/list）│
└──────────────────┬─────────────────────────────────┘
                   ▼
┌─ 关卡 1：白名单式合并（merge_section_update :703）───┐
│ dict 模块：字段级合并，未提及字段保留                  │
│ list 模块：按条目 id 合并（_merge_list_section :648） │
│   · id 匹配 → 原地修补，位置不动                      │
│   · 未提及 → 原样保留                                │
│   · removed_ids → 删除                              │
│   · 陌生/缺失 id → 强制当新条目追加（防重排/重编号）    │
└──────────────────┬─────────────────────────────────┘
                   ▼
┌─ 关卡 2：空变化检测 ─────────────────────────────────┐
│ merged == current → 拒"没有实际内容变化"              │
└──────────────────┬─────────────────────────────────┘
                   ▼
┌─ 关卡 3：数字防幻觉（_numeric_claims :528）──────────┐
│ _numeric_claims(方案) - _numeric_claims(证据) 非空 → 拒│
│   证据 = 简历原文 + 最近对话 + 用户本轮消息            │
│   数字归一化：2025.9 ≡ 2025-09；跳过 id/layout 等结构键│
│   图片附件轮次豁免（多模态读图数字查不到文本出处）      │
└──────────────────┬─────────────────────────────────┘
                   ▼
       关卡 4：_actual_change_suggestions (:856)
       从真实 diff 生成人读摘要（不信模型自述）
       → 返回完整合并简历 + suggestions（不落库）
```

设计思想：backend **不接受模型给的整份简历，只接受改动声明**，在白名单框架内自己算结果。

## 8. 确认（decision）流程

```
前端点确认 → POST decision {action: "apply"}（不带简历数据）
  ▼
resolve_chat_change (:939)
  ├─ db.get(message_id) + 归属/pending 校验（重复确认 409）
  ├─ _persist_resume_change (:904)
  │    ├─ 先写 ResumeVersion 快照（可回滚）
  │    ├─ resume.resume_data = 落库时的完整快照（不再重新合并！）
  │    └─ 写入后回读校验（:931-934）
  └─ 消息状态 → applied，响应返回更新后的简历 JSON → 前端刷新编辑器
```

## 9. 校验辅助函数速查

| 函数 | 位置 | 职责 |
|---|---|---|
| `merge_section_update` | :703 | 提案→完整快照（id-bounded；旧端点 `/update-section` 保留，Agent 已不走） |
| `_merge_confirmed_resume_data` | :441 | 提案→完整快照（basics 路径，字段级保守合并，防丢 avatar 等配置；旧端点 `/update-basics-field` 保留） |
| `_unwrap_resume_data` | :479 | 剥掉模型可能包的外壳（resume_data/data/result/…） |
| `_numeric_claims` | :528 | 从简历 JSON 抽取全部数字 token（含变体归一化） |
| `_unverified_numeric_claims` | :550 | 方案数字 − 证据数字（集合差），非空即幻觉 |
| `_actual_change_suggestions` | :856 | 新旧 diff → 人读变更摘要（存 suggestions，三处消费：工具转述/确认卡片/下轮上下文） |
| `build_ai_service_context` | :151 | 简历全文 + `schema_compact()` 字段清单 + history + pending 摘要 |
