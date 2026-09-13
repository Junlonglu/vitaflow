# VitaFlow — pi-agent SDK 深度分析：Prompt 分层与 Context 管线

> 面试向文档：ai-service 从 LangChain 迁到 pi-agent 后的 SDK 内核机制
> （Prompt 资源注入钩子、斜杠命令、Context → 模型请求体的完整转换链）

---

## 1. 为什么需要理解这套机制

ai-service 是无状态的 Node 检索服务：每个对话回合新建一个 pi 会话，
历史/简历快照由 FastAPI 重新注入。这意味着「模型看到什么」完全由三层东西决定：

1. **systemPrompt**（人设 + 规则 + 注入的上下文）
2. **本回合 user 消息**（自然语言原文，或斜杠命令展开后的指令）
3. **Context**（pi SDK 内部的结构化请求载体，最终序列化成 HTTP 请求体）

面试常问的「你的 prompt 是怎么组织的」「工具调用是怎么循环的」，
答案都在这条管线里。

---

## 2. ResourceLoader 的三个 override（资源注入钩子）

### 2.1 默认行为（不用 override）

pi-agent 本体是编码 agent，`DefaultResourceLoader.reload()` 会从磁盘自动发现资源：

| 资源 | 默认来源 | 作用 |
|---|---|---|
| systemPrompt | 内置编码助手人设（含各工具的 prompt 片段） | 系统消息 |
| appendSystemPrompt | `AGENTS.md` 类文件、扩展注入的追加片段（string 数组） | 拼在 base system prompt 之后 |
| prompts | `~/.pi/agent/prompts/`、`<cwd>/.pi/prompts/` 下的模板文件 | 斜杠命令库 |

### 2.2 三个 override 的签名与语义

```ts
// pi-coding-agent/dist/core/resource-loader.d.ts
systemPromptOverride?:       (base: string | undefined) => string | undefined
appendSystemPromptOverride?: (base: string[]) => string[]
promptsOverride?:            (base: { prompts: PromptTemplate[]; diagnostics }) => 同构
```

本质都是「拿到默认加载结果 base，返回替换后的结果」。
生效点在 agent-session.js:644：

```ts
this.agent.state.systemPrompt = this._systemPromptOverride ?? this._baseSystemPrompt;
```

### 2.3 vitaflow 的用法（ai-service/src/agent.ts:302-318）

```ts
const loader = new DefaultResourceLoader({
  cwd, agentDir,
  systemPromptOverride: () => systemPrompt,        // ① 整体替换人设
  appendSystemPromptOverride: () => [],            // ② 清空追加片段
  noContextFiles: true,
  noSkills: true,
  promptsOverride: (base) => ({                    // ③ 追加斜杠命令
    prompts: [...base.prompts, ...buildQuickCommandTemplates().map(toPromptTemplate)],
    diagnostics: base.diagnostics,
  }),
});
```

| override | 如果去掉会怎样（可见差异） |
|---|---|
| ① systemPromptOverride | system 变成 pi 内置编码助手人设 + read/edit/bash 工具说明——「修改简历」「表达边界」等规则全消失 |
| ② appendSystemPromptOverride | system 尾部可能多出 AGENTS.md / 扩展追加的无关片段 |
| ③ promptsOverride | `/润色 工作经历` 匹配不到模板，用户消息就是字面 7 个字符，模型收到一句没头没尾的话 |

三层各管一件事：① 定「我是谁/守什么规矩」，② 管「不掺杂质」，③ 管「用户可以输入什么快捷指令」。

---

## 3. QuickCommand：命令 prompt 与 systemPrompt 完全独立

### 3.1 结论

- **systemPrompt 是系统层（固定）**：backend 按需组装（或用 ai-service 的
  `DEFAULT_SYSTEM_PROMPT`），经 `runChat` 传入，通过 ① 注入，每回合原样不变。
- **QuickCommand 是指令层（按回合展开）**：`buildQuickCommandTemplates()`
  （commands.ts）里 5 个模板通过 ③ 注册。只在用户消息以 `/润色` 等开头时，
  pi-agent 把模板 content 展开成**本回合的 user 消息**。
- **组合方式**：模型每次看到 `system（不变）+ user（命令时 = 展开后的指令）`。
  命令不是切换 systemPrompt，而是在 user 消息层「替用户写一段更精确的指令」，
  之后走和自然语言完全相同的工具/校验链——所以命令行为仍受 systemPrompt
  里的全局约束（不编造事实、提案需确认）管着，两条路径不会产生两套规则。

### 3.2 无状态设计下的最终视角

pi 会话是 `SessionManager.inMemory` 每回合新建的，**历史不在 messages 里，
而是被 backend 注入到 systemPrompt 里**。以 `/润色 工作经历` 为例，模型收到：

```
messages[0] — system（buildContextPrompt 把 4 块 context 拼进 DEFAULT_SYSTEM_PROMPT）
  ├── # 修改简历 / # 表达边界 / # 图片输入 / # 输出规范   ← DEFAULT_SYSTEM_PROMPT 本体
  ├── # 当前简历快照        ← context.resume
  ├── # 更早的对话摘要      ← context.context_summary（滚动摘要压缩任务产出）
  ├── # 最近的对话          ← context.history（条数跟随模型策略）
  └── # 待确认的简历修改    ← context.pending_change

messages[1] — user（命令展开产物，模板 content 中 $@ → 工作经历）
  # 润色指令
  用户用 /润色 做检查与表达润色（只改善措辞，不改变事实）。检查范围：工作经历。
  请执行：1. get_resume_context 读取范围内模块……
```

之后才是普通 agent 循环：assistant 调 `get_resume_context` → tool result →
输出问题清单/提案 → `propose_change` → 结束回合。

### 3.3 命令展开的实现细节

```
session.prompt("/润色 工作经历")
  → agent-session.js:825  expandPromptTemplate(text, this.promptTemplates)
  → prompt-templates.js:224  /^\/([^\s]+)(?:\s+([\s\S]*))?$/   ← [\s\S]* 捕获整个剩余部分
  → substituteArgs(content, args)   ← $1/$2 位置参数、$@/$ARGUMENTS 全部参数
```

- 参数按空白分词：`parseCommandArgs("工作经历")` → `["工作经历"]`。
- `/JD优化 <粘贴整段JD>` 能工作的原因：正则用 `([\s\S]*)` 捕获多行文本整体。
- 历史在 system 而非 messages ⇒ 命令展开文本永远是本回合第一条 user 消息，
  紧贴最新简历快照，不会淹没在长历史里（无状态设计的附带好处）。

---

## 4. Context 是什么，如何变成模型请求

### 4.1 Context 接口（pi-ai types.d.ts:362）

```ts
interface Context {
  systemPrompt?: string;   // 系统提示词
  messages: Message[];     // UserMessage | AssistantMessage | ToolResultMessage
  tools?: Tool[];          // 可用工具的 JSON Schema 定义
}
```

**名字撞车提醒**：vitaflow 业务代码里的 `context`（FastAPI 传入的简历快照/
历史/待确认变更）**不是** pi 的 Context——它在 server.ts:297 被
`buildContextPrompt` 拼进 systemPrompt 字符串。pi 的 Context 是 SDK 内部的
结构化请求载体，vitaflow 真正喂给它的只剩一个字符串 + 工具表。

### 4.2 三层转换管线

```
AgentSession（pi-coding-agent）       agent-loop.js（pi-agent-core）      openai-completions.js（pi-ai）
AgentMessage[] 转写本                 组装 Context 并调用 streamFn         Context → HTTP 请求体
──────────────────                   ───────────────────                ───────────────────
agent.state = {                      streamAssistantResponse (L178-198):  convertMessages (L764):
  systemPrompt ← override ?? base ──►  transformContext                  ──► systemPrompt → 第一条 system 消息
  messages: AgentMessage[]            convertToLlm: AgentMessage[]           user → {role:"user"}
  tools ← 会话实际注册的工具             → Message[]（只留                      assistant → {role:"assistant", tool_calls}
  （每次请求前 prepareNextTurn           user/assistant/toolResult,           toolResult → {role:"tool"}
   重新注入，agent-session.js:271）      bashExecution/custom 等                + buildParams(L512) 补 model/stream/
                                       转 user 或丢弃）                        thinking/tools 等字段
                                    → llmContext = {systemPrompt,
                                       messages, tools}
                                    → streamFn(model, llmContext, opts)
```

关键转换：

- **convertToLlm**（pi-coding-agent messages.js:75）：会话转写本里有比 LLM
  认识的更多的角色（bashExecution、custom、branchSummary、compactionSummary），
  统一转成 user 消息或丢弃；vitaflow 的每回合新会话里基本只剩
  user / assistant / toolResult。
- **convertMessages**（pi-ai openai-completions.js:764）：按 provider compat
  分支序列化——system 用 `system` 还是 `developer` 角色、thinking 块回放成
  `reasoning_content` 还是纯文本、toolCallId 规范化等，全是适配层的事，
  业务代码不用关心。

### 4.3 具体例子：`/润色 工作经历` 回合的第二轮请求

**第 0 层 — 会话转写本 `agent.state.messages`（AgentMessage[]）：**

```ts
[
  { role: "user",      content: "# 润色指令\n\n……检查范围：工作经历。" },
  { role: "assistant", content: [ { type: "text", text: "先读取工作经历……" },
                                  { type: "toolCall", name: "get_resume_context",
                                    arguments: { sections: ["work"] } } ] },
  { role: "toolResult", toolCallId: "call_1", toolName: "get_resume_context",
    content: [{ type: "text", text: '{"work":[…简历条目…]}' }], isError: false },
  { role: "assistant", content: [ { type: "thinking", thinking: "该条目表达无力…" },
                                  { type: "toolCall", name: "propose_change", arguments: {…} } ] },
  { role: "toolResult", toolCallId: "call_2", toolName: "propose_change",
    content: [{ type: "text", text: "提案已创建，等待用户确认" }], isError: false },
]
```

**第 1 层 — agent-loop.js:187-191 组装的 `llmContext`：**
三个字段原样打包（systemPrompt 来自 `override ?? base`）。

**第 2 层 — 实际 HTTP 请求体（deepseek 走 openai-completions 适配层）：**

```jsonc
POST https://api.deepseek.com/chat/completions
{
  "model": "deepseek-v4-flash-vision-exp",
  "stream": true,
  "stream_options": { "include_usage": true },
  "messages": [
    { "role": "system", "content": "你是 VitaFlow 平台的简历 AI 助手……\n\n# 当前简历快照\n- 求职者：张三｜后端工程师……" },
    { "role": "user", "content": "# 润色指令\n\n……检查范围：工作经历。" },
    { "role": "assistant", "content": null,
      "tool_calls": [{ "id": "call_1", "type": "function",
        "function": { "name": "get_resume_context", "arguments": "{\"sections\":[\"work\"]}" } }] },
    { "role": "tool", "tool_call_id": "call_1", "content": "{\"work\":[…] }" },
    { "role": "assistant", "content": null, "reasoning_content": "该条目表达无力……",
      "tool_calls": [{ "id": "call_2", "type": "function",
        "function": { "name": "propose_change", "arguments": "…" } }] },
    { "role": "tool", "tool_call_id": "call_2", "content": "提案已创建，等待用户确认" }
  ],
  "tools": [
    { "type": "function", "function": { "name": "get_resume_context", "description": "…", "parameters": {…} } },
    { "type": "function", "function": { "name": "update_section", … } },
    { "type": "function", "function": { "name": "propose_change", … } }
  ],
  "thinking": { "type": "enabled" },
  "reasoning_effort": "medium"
}
```

> thinking 参数的来源链：models.json `"reasoning": true`（能力开关）→
> agent.ts `thinkingLevel: model.reasoning ? "medium" : "off"` →
> pi-ai compat 按 provider 自动检测（deepseek → thinkingFormat "deepseek"）
> → 请求体带 `thinking: {type:"enabled"}` + `reasoning_effort`。

### 4.4 工具循环 = 多次完整重发

agent-loop 每收到 `stopReason: "toolUse"` 就执行工具、把结果 push 进
`context.messages`，然后**重新发整个 Context**。上面的请求体是第二轮 HTTP
请求（第一轮只到 call_1 之前）。推论：

- 每个工具回合 thinking 上下文都在重算——这是「模型生成（含思考）」耗时
  长的原因之一（另有 reasoning_effort: "medium" 的 token 开销）。
- 回合内 HTTP 请求次数 = 工具调用轮数 + 1，usage 聚合也按此累积。

---

## 5. 面试要点速记

| 问题 | 一句话答案 |
|---|---|
| systemPrompt 和斜杠命令什么关系？ | 不同层：system 固定管规则，命令只是展开成本回合 user 消息，共用同一套工具链 |
| 无状态会话怎么做多轮？ | 每回合新建 in-memory session，历史/简历快照由 FastAPI 注入 systemPrompt（buildContextPrompt） |
| pi 的 Context 是什么？ | `{systemPrompt, messages, tools}` 三元组，经 convertToLlm → provider 适配层序列化成 HTTP 请求体 |
| AgentMessage 和 Message 的区别？ | 前者是会话转写本（含 bashExecution/custom 等富角色），后者是 LLM 认识的三种角色，convertToLlm 负责转换 |
| 工具调用的请求成本？ | 每轮工具循环完整重发整个 Context；N 次工具调用 = N+1 次 HTTP 请求，token 按 input 重复计费（有缓存时看 cacheRead） |
