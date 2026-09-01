# MySQL 为会话事实源，Agent 会话请求级重建

status: accepted
date: 2026-09-01

pi-agent 的 SessionManager 默认落盘 `~/.pi/agent/sessions/`，是 CLI 单用户设计；Web 多用户场景下不可用。同时前端的消息列表、待确认变更（pending_change）、apply/reject 决策流都建立在现有 `ai_chat_sessions` / `ai_chat_messages` 表之上。

我们决定：MySQL 现有表保持唯一事实源；pi 会话按请求创建（`SessionManager.inMemory()`），上下文（简历快照、历史消息、待确认变更）由 FastAPI 按 `context_messages` 配置注入，请求结束即弃。token 归集与计费入账由 AI 服务从 pi 事件流读取后回调 FastAPI。

## Considered Options

- **pi SessionManager 落库 + 与现有表双写**：两份事实源需要同步协议，一致性与调试成本高，拒绝。
- **MySQL 事实源 + 请求级重建**（采纳）：多用户安全、UI 与决策流零改动、无状态 AI 服务可水平扩展；代价是每请求重建上下文——但上下文本就来自 DB 查询，与现状同构。

## Consequences

- pi 的跨请求会话记忆（compaction、历史压缩）不使用；长对话历史裁剪由 FastAPI 的 `context_messages` 上限控制。
- AI 服务必须无状态化，会话亲和性不依赖内存。
