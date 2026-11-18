# Agent 轨迹格式（RSI 前置规划）

> 目的：在还没有 RSI 需求的时候就定好轨迹的落盘格式，避免将来要"重放一个回合"时发现关键字段根本没存。
> 状态：**P0 已落地（2026-11-13）**，P1/P2 待产品与合规确认。
>
> 本文只定"存什么"；"怎么用这批数据做改进"见 `rsi-plan.md`（2026-11-17）。那份文档核对真实轨迹后，在下面的 P1 之前插入了 P1'（纯格式补齐，见 §5），并纠正了两处对现状的假设：`reason_chars` 一直是 0、`outcome` 从不因工具失败变 `failed`。

## 1. 现在存了什么，缺什么

P0 落地之前，一次对话回合分散在三处（下面四条缺口里的 2 和 4 已由 P0 补上，3 由 P2 的 `feedback` 事件补）：

| 位置 | 内容 | RSI 视角的问题 |
|---|---|---|
| `ai_chat_messages.content` | 最终回复 | 只有结果，没有决策过程 |
| `ai_chat_messages.tool_trace` | 活动行：`name` / `label` / `detail` / `duration_ms` / `kind` | **`label` 是给人看的策展文案，原始 `args` 和工具返回全被丢掉了**，决策点无法回放 |
| `ai_tasks.input_data` | 模型、token、`tool_calls` 计数、`tool_quality`、`turn_timing` | 有统计口径，但按 LLM 调用拆行，和"回合"不是一对一 |
| `ai_chat_messages.action_status` | 用户应用 / 放弃 | 这是最有价值的隐式反馈，但目前和轨迹没有结构关联 |

四个硬缺口：

1. **决策点丢失**：只知道"改了两个条目"，不知道模型传了什么 JSON、为什么这么传。这是 RSI 唯一能学到东西的地方。
2. **版本不可归因**：改完提示词之后，前后效果无法对比——因为没有任何字段标识"这一轮用的是哪一版提示词/字段结构/技能"。
3. **反馈不闭环**：`action_status` 挂在消息上，轨迹挂在别处，两者只能靠人肉 join。
4. **没有 trace_id**：一条消息、多条 `ai_tasks`、若干工具步骤，缺少一个稳定主键把它们串起来。

## 2. 格式选型：事件流，不是回合快照

两种候选：

- **回合快照**（一行一个回合，内含 steps 数组）：查询方便，但写入必须等回合结束，中途失败就没记录；feedback 晚几天回来只能改行。
- **事件流**（一行一个事件，append-only）：天然可流式写、可增量导出、feedback 可以事后追加；分析时按 `trace_id` group by。

选事件流。它同时满足三件事：实时落盘不丢、训练管道直接吃 JSONL、后续加新事件类型不用改表结构。

## 3. Schema

一行一个 JSON 对象，UTF-8，JSONL。所有行共有：

| 字段 | 类型 | 说明 |
|---|---|---|
| `v` | int | schema 版本，从 `1` 开始。混存不同版本时按行读 `v` |
| `trace_id` | string | 一次回合一个 uuid，贯穿到 feedback |
| `seq` | int | 事件序号，从 0 单调递增 |
| `type` | string | `turn_start` / `phase` / `thinking` / `text` / `tool_call` / `tool_result` / `turn_end` / `feedback` |
| `ts` | int | 绝对毫秒时间戳（对齐跨系统日志用） |
| `t_ms` | int | 相对回合开始的毫秒偏移（分析主用这个） |

### 3.1 `turn_start` — 一回合的头部

```json
{"v":1,"seq":0,"type":"turn_start","trace_id":"01JC...","ts":1763020800000,"t_ms":0,
 "turn_id":12345,"session_id":9,"resume_id":42,"user_ref":"u:7",
 "trigger":"skill_command",
 "skill":{"id":"diagnose","name":"诊断"},
 "versions":{"rules":"r7f3a2c91d0e","schema":"s1b2c3d4e5f6","prompt_chars":6377,"agent":"ai-service@0.1.0"},
 "model":{"provider":"deepseek","model":"deepseek-v4-flash","thinking":"off"},
 "context":{"resume_chars":5292,"history_turns":6,"summary_chars":0,"skills_declared":2},
 "input":{"chars":22,"attachments":0}}
```

关键点：

- **`versions` 必须是分段 hash，不能是整份提示词的 hash。** 系统提示词里带当前简历，每轮都变，整份 hash 永远不同，等于没有版本概念。要分开三份：
  - `rules` = 常驻规则文本的 hash
  - `schema` = `schema_compact()` 输出的 hash
  - `skill` = 技能 id（在 `skill` 字段里）
  三者组合才能回答"这次效果变化是规则改的、字段结构改的、还是换技能导致的"。
- `trigger` 枚举：`skill_command` / `free_text` / `regenerate` / `question_answer`。技能卡点击和手打 `/诊断` 都算 `skill_command`。
- `input` 默认只存长度，原文进 L2（见 §4）。

### 3.2 `tool_call` — 决策点，最重要的一行

```json
{"v":1,"seq":3,"type":"tool_call","trace_id":"01JC...","ts":1763020800820,"t_ms":820,
 "call_id":"tool:1763020800820:5m1puu3awi9","name":"update_resume",
 "args_digest":{"sections":["basics"],"items":0,"fields":1,"from_library":0,"args_chars":188},
 "args_ref":null}
```

- `args_digest` 是**结构性摘要**：模块列表、条目数、字段数、参数字符数。不带任何简历内容，可永久留存，足够做"模型偏好改哪些模块""平均每次改几条"这类分析。
- `args_ref` 指向受控对象存储里的原文（`blob://trace/<trace_id>/3.json`），默认 `null`。训练需要原文时才开（§4）。
- 拒绝存 `label`：那是 UI 文案，改一次措辞就污染历史数据。要分析"改了哪个模块"，看 `args_digest.sections`。

### 3.3 `tool_result`

```json
{"v":1,"seq":4,"type":"tool_result","trace_id":"01JC...","ts":1763020800960,"t_ms":960,
 "call_id":"tool:1763020800820:5m1puu3awi9","name":"update_resume",
 "ok":true,"category":null,"reason_chars":0,"duration_ms":140}
```

`category` 沿用现有错误信封（`validation` / `business` / `infra`），这是 RSI 判断"该不该重试、该不该换策略"的直接信号。

**但只有 `category` 不够，而且 `reason_chars` 帮不上忙。** `agent.ts` 只在 `event.isError`（工具抛错）时算 `reasonChars`；业务校验失败走的是 `details.ok === false`，所以 6 次失败的 `reason_chars` **全是 0**，轨迹里**没有 `reason` 字段**（不是存了 null）。上游响应其实一直带着 `reason` 原文（FastAPI `success({"ok":false,"reason":<issue>})`），是 `trace.ts` 的 `toolResult` 只接长度、把它丢了。

所以"字段名不合法"和"提案为空"这两种完全不同的修法，目前无法区分——**归因阶段拿不到可用的输入**。

补齐方式（R0，见 `rsi-plan.md` §6）：**不改后端协议**，把 `tools/resume.ts` 已读到的 `details.reason` 归一成枚举再落库。

```json
{"ok":false,"category":"validation",
 "reason_code":"empty_proposal",
 "reason_path":"changes[0].fields",
 "reason_chars":88}
```

- `reason_code` 用枚举（`empty_proposal` / `unknown_field` / `type_mismatch` / `id_not_found` / `no_effective_change` …），**不存错误原文**，也不存字段值。
- `reason_path` 只到字段路径，不带内容（≤200 字符），可以进 L1 永久留存。
- `reason_chars` 保留，用于判断错误信息是否过长（过长通常是上游把整段 schema 吐回来了）。

### 3.4 `turn_end`

```json
{"v":1,"seq":9,"type":"turn_end","trace_id":"01JC...","ts":1763020804200,"t_ms":4200,
 "outcome":"ok","timing":{"first_token_ms":537,"tool_ms":140,"total_ms":4200},
 "usage":{"input":6100,"output":320,"cache_read":2560},"reply_chars":412,
 "steps":{"tool_calls":1,"skills":1,"thinking_chars":0}}
```

`outcome` 为 `failed` 时补 `error":{"message"}`（message 截断 400 字；`trace.ts` 的 `end()` 目前只写 message，不写 category）。

**`outcome` 是传输层口径，不是任务口径。** 实现上（`ai-service/src/agent.ts`）只有"模型不可用 / 流式中断"才写 `failed`；工具调用失败不影响它。实测 7 条轨迹 `outcome` 全是 `ok`，包括两条 **3 次工具调用全部失败** 的回合——模型回了话就算 ok。

所以需要新增一个独立字段，别改 `outcome` 的语义（计费与告警都在读它）：

```json
{"task_success":false,"task_success_basis":"all_tools_failed"}
```

`task_success_basis` 取值：`applied`（用户点了应用）/ `all_tools_failed` / `tool_failed`（部分失败）/ `no_tool_call` / `unknown`。宁可给 `unknown`，也不要把"没报错"当成功——错误的正负样本比例会直接污染后续所有评估。

### 3.5 `feedback` — 可以晚于 `turn_end` 很久

```json
{"v":1,"seq":10,"type":"feedback","trace_id":"01JC...","ts":1763020813000,
 "action":"applied","delay_ms":8800,"edited_after_apply":false,"edited_fields":0}
```

`action` 枚举：`applied` / `rejected` / `ignored`。
`edited_after_apply`：应用之后用户又手动改了同一份简历的同一模块，说明模型改得不够好——这是比 apply/reject 更细的负反馈，需要版本快照对比（P2）。

## 4. 隐私：两条轨道

简历是个人数据，轨迹里必然带 PII。所以拆两层，用同一个 `trace_id` 关联：

| 层 | 内容 | 留存 | 用途 |
|---|---|---|---|
| **L1 meta** | 结构、计数、耗时、成败、版本、`args_digest` | 永久 | 看板、回归评估、大部分 RSI 信号 |
| **L2 payload** | 用户原文、工具 args 原文、模型输出原文 | 默认关闭；开启后 30 天 TTL，对象存储独立加密 | 需要"重放决策"的训练样本 |

三条硬规则：

1. **L2 必须是显式开关**（服务级 flag + 用户协议覆盖），不能默认开。
2. L2 只进对象存储，不进 MySQL——避免 `SELECT *` 类的误导出。
3. 用户删除简历/注销时，按 `trace_id` 反查 L2 一并删除；L1 因为不含 PII 可以留（这也是把 `args_digest` 设计成纯结构的原因）。

## 5. 落地分期

### P0（已落地，2026-11-13）

| 落点 | 文件 | 说明 |
|---|---|---|
| 事件采集 | `ai-service/src/trace.ts` | `createTurnTrace()` 在工具开始/结束与回合收尾各写一条事件；`argsDigest()` 生成参数结构摘要；`versionTag()` 生成分段 hash |
| 接入点 | `ai-service/src/agent.ts` | `AgentInput.trace` 可选注入；`runChat` 在 `tool_execution_start/end` 与三条收尾路径（模型不可用、fallback、正常结束）上报 |
| 版本与元信息 | `ai-service/src/server.ts` | rules / schema / skill 三段 hash + 模型 + 上下文体积 + turn_id/session_id |
| 上报 | `ai-service/src/trace.ts` `reportTrace()` | 回合末尾整包 POST `/api/ai/service/trace`，带一次廉价重试（对比计费的三次退避） |
| 落库 | `backend/app/models/ai_trace.py` + `alembic/versions/0040_agent_traces.py` | 一行一回合；`events` JSON、`prompt_versions` JSON、`event_count`/`model`/`outcome` 冗余列供看板 GROUP BY |
| 端点 | `backend/app/api/ai.py` `POST /service/trace` | service token 鉴权；按 `trace_id` 幂等覆盖；events 上限 200 |
| 关联键 | `backend/app/services/ai_chat_service.py` | 转发时带上 `turn_id`（被回复的用户消息 id）/`session_id`/`trigger` |
| 导出 | `backend/scripts/export_traces.py` | 表行摊平成事件流 JSONL，stdout 干净可管道 |

两个实现取舍，值得写下来：

1. **整包上报，不逐事件推。** 文档最初写"实时落盘不丢"，但 P0 只有 L1、事件是个位数，逐事件推送会把链路复杂度抬到计费回调那一级，而收益只是"进程崩溃时保住半条轨迹"——那种回合本来也没有分析价值。逐事件写留给 P1 的 L2 通道。
2. **`turn_id` 是用户消息 id，不是 assistant 行 id。** ai-service 在回合开始时只知道被回复的是哪条用户消息；assistant 行要等 result 返回到 FastAPI 才写入。用用户消息 id 关联，两边都拿得到，且一轮对话只有一条。

`/v1/task`（生成/导入简历的一次性任务）暂不采集：它没有工具调用，决策信息量远低于对话回合，等 L2 通道一起做。

### P1'（不需要产品确认，纯格式补齐，建议先做）

前两项只改事件字段，不涉及 PII，也不依赖 L2 开关；第三项是导出脚本加参数。三者是后续所有改进动作的前提（见 `rsi-plan.md` §6 R0），所以排在 P1 之前，用字母编号不占 P1 的数字：

a. `tool_result` 补 `reason_code` + `reason_path`（从 `tools/resume.ts` 已拿到的 `details.reason` 映射，不含原文），见 §3.3。
b. `turn_end` 补 `task_success` + `task_success_basis`，见 §3.4。
c. `backend/scripts/export_traces.py` 加 `--bucket skill|task_success`（`outcome` 现在恒为 `ok`，不能当分桶键），能一条命令导出"某技能的全部失败回合"。

### P1（需要产品确认）

5. L2 payload 开关 + 对象存储写入 + TTL 清理任务。
6. `feedback` 事件：`apply/reject` 接口里按 `turn_id` 找到 `trace_id` 追加一行。

### P2（有明显样本量之后）

7. `edited_after_apply`：对比 apply 后的 `ResumeVersion` 快照与模型产出，算字段级差异。
8. 评估集：从 L1 里抽"applied 且无后续编辑"的样本做正例，`reason_code` 明确的失败做负例（**别用 `category=validation`**——它现在把"空提案"也算进去了，见 `rsi-plan.md` §2.1）。切分时**选择集与测试集不重叠**（`rsi-plan.md` §5）；每技能 30–50 条起步，100 条以下不做迭代。

## 6. 现在就别做错的三件事

1. **不要把 `label` 当轨迹字段**。它是 UI 文案，随时会改（这次刚从"使用技能"改成"加载技能"）。分析要用 `name` + `args_digest`。
2. **不要用整份 systemPrompt 做版本 hash**。带简历的提示词每轮都不同，hash 出来的"版本"没有任何信息量。
3. **不要把 L2 直接塞进 `ai_chat_messages` 的 JSON 列**。消息表会被高频查询，PII 原文放进去等于把删除义务扩散到每个读路径。

## 7. 与现有字段的映射

| 新字段 | 现状来源 |
|---|---|
| `turn_id` | `ai_chat_messages.id`（**被回复的用户消息**；assistant 行要等 result 才写入，ai-service 当时拿不到） |
| `timing` | `ai-service` 的 `buildTurnTiming()`，已落 `ai_chat_messages.timing` |
| `usage` | `createUsageAggregator()` → `/service/usage` 的 `usage[]` |
| `tool_calls` / `tool_quality` | `ai_tasks.input_data`，可直接搬 |
| `skill` | `server.ts` 的 `skillCommand.skill.id`（已用于计费 feature） |
| `action` | `ai_chat_messages.action_status` |
| `outcome` | `ChatTurnOutcome.failed`（**只覆盖模型不可用与流式中断**）。原表写的"toolQuality 失败项"实现里没有接进来，2026-11-17 实测 6 次工具失败的回合仍是 `ok`——门控在 `agent.ts` 的 `trace?.end({..., outcome:"ok"})`，补 `task_success` 而不是改 `outcome` |
| `task_success` | 无现成来源，需在 `agent.ts` 收尾时按 `toolQuality` + 是否有 apply 推导 |
