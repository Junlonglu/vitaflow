# Node 旁路服务承载 pi-agent

status: accepted
date: 2026-09-01

pi-agent 是 Node.js/TypeScript SDK，而 VitaFlow 后端是 Python（FastAPI），两者无法进程内共存。我们决定新增一个独立的 Node.js「AI 服务」承载 pi-agent 运行时，FastAPI 保留认证、数据库、计费、文件导出（Playwright/WeasyPrint）等全部既有职责，通过 HTTP/SSE 调用 AI 服务。信任边界（四层防御的校验落库）保留在 Python 侧。

## Considered Options

- **全量迁移 Node 后端**：工程量巨大，且会丢失 Python 生态（Playwright 导出、WeasyPrint、pytesseract），拒绝。
- **混合双栈**（Node 只管对话，其余功能仍在 Python 进程内调 LLM）：两套 LLM 客户端并存，计费与日志割裂，与「完全重写」目标冲突，拒绝。
- **Node 旁路服务**（采纳）：边界清晰，Python 生态不动，pi-agent 升级只影响单一服务。

## Consequences

- docker-compose 新增 `ai-service` 容器，双语言部署。
- 服务间需要轻量认证（内网 + 服务令牌）。
- 前端不直连 Node 服务，全部流量经 FastAPI 代理（见 ADR-0003 的流式决策）。
