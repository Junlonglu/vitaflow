# VitaFlow

智能简历生成与优化平台。本文档定义 AI 模块重构（pi-agent 化）讨论中的统一术语。

## Language

**简历对话（Resume Chat）**:
AI 交互的统一入口。所有 AI 能力（修改、润色、翻译、优化、生成）都通过对话完成，不再有独立的 功能按钮/API。
_Avoid_: AI 助手功能、聊天功能

**快捷指令（Quick Command）**:
对话内的斜杠命令（如 `/润色`），封装高频操作的提示词模板。对应 pi-agent 的 PromptTemplate 机制。
_Avoid_: 快捷方式、按钮功能

**AI 服务（AI Service）**:
承载 pi-agent 运行时的 Node.js 旁路服务。FastAPI 通过 HTTP/SSE 调用它；认证、数据库、计费仍归 FastAPI。
_Avoid_: AI 模块、Agent 服务

**待确认变更（Pending Change）**:
Agent 提出的简历修改提案，处于用户尚未确认的状态。用户点「应用」后才写入简历。
_Avoid_: 修改方案（仅指提案内容本身时可用）

**增量修改（Incremental Update）**:
Agent 通过工具调用只提交被修改的内容（如单个 section），而非重发整份简历数据。解决全量重传导致的 30~50s 延迟。
_Avoid_: 局部更新、diff 修改

**四层防御（Four-Layer Guard）**:
保证未要求修改的内容不被改坏的机制组合：Prompt 约束、快照保护、分级合并、校验+diff+人审。重构后语义必须保留，实现位置可变。
_Avoid_: 校验层、安全层

**Flow Points**:
用户消耗 AI 服务的点数货币，按模型粒度计费（每次调用 + 百万 token 单价）。重构中不变。

## Retired

**简历评分（Score Resume）**:
已决定移除的功能（单次调用约 51s，独立价值低）。
