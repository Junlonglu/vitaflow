# VitaFlow AI 服务

承载 pi-agent 运行时的 Node.js 旁路服务。FastAPI 通过 HTTP/SSE 调用它，并保留认证、数据库、计费（见 `docs/pi-agent重构设计.md`）。

## 当前状态（Ticket 01 骨架）

只打通「纯对话回复」的读链路：

- 独立进程，接受 `POST /v1/chat`，返回 NDJSON 事件流（`start` / `phase` / `delta` / `result` / `error`），与前端既有流式契约一致。
- **默认 Faux Provider 模式**：无网络、确定性输出，测试与演示不依赖真实模型，非确定性被隔离。
- 会话为请求级 `SessionManager.inMemory()`，用后即弃，无状态可水平扩展。
- **零工具白名单**：`tools: []`，不暴露任何修改路径（纯只读）。
- 覆盖 pi 默认编码助手人设，改为简历助手系统提示词。
- 服务间鉴权：静态令牌（`Authorization: Bearer <token>`）。
- 真实 Provider（models.json / deepseek）为后续票接入，当前返回 501。

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

```bash
docker compose --profile ai-service build ai-service
docker compose --profile ai-service up -d ai-service
```

> 使用 `ai-service` profile，避免影响默认 `docker compose up -d` 的参考基础设施。

## 常用命令

| 命令 | 说明 |
|------|------|
| `npm run dev` | 热重载启动（tsx watch） |
| `npm start` | 一次性启动（tsx） |
| `npm run typecheck` | TS 类型检查 |
