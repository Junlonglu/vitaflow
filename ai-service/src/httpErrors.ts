/**
 * HTTP 层错误出口。
 *
 * Express 4 不接管 async handler 的拒绝：handler 里任意一个 await 抛错都会
 * 变成 unhandledRejection，而 Node 22 默认把它升级成进程退出——一个异常请求
 * 就能带走整个 AI 服务，同进程里所有在途回合一起断，用户侧全是半截回答。
 * 所以每个 async 路由都必须经由 `runHandler` 执行，而不是直接交给 Express。
 */
import type express from "express";
import type { ChatStreamEvent } from "./agent.js";

/** 写一行 NDJSON；socket 已经关掉时静默丢弃（客户端断开是常态，不是错误）。 */
export function sendEvent(res: express.Response, event: ChatStreamEvent): void {
  if (res.writableEnded) return;
  res.write(`${JSON.stringify(event)}\n`);
}

/**
 * 执行一个 async 路由，并在它抛出时给出「客户端能读懂」的结局。
 *
 * 两种情形必须分开处理：
 * - 还没开始写响应 → 正常返回 500 JSON，前端能弹出错误而不是解析一个空流；
 * - 已经进入流式阶段（`headersSent`）→ 状态码改不了，补一个 error 事件再收尾，
 *   否则客户端会一直挂着等一个永远不来的 result。
 */
export async function runHandler(
  label: string,
  res: express.Response,
  handler: () => Promise<void>,
): Promise<void> {
  try {
    await handler();
  } catch (err) {
    console.error(`[vitaflow-ai] ${label} handler crashed:`, err);
    if (res.headersSent) {
      sendEvent(res, { type: "error", message: "AI 服务内部错误" });
      res.end();
      return;
    }
    res.status(500).json({ type: "error", message: "AI 服务内部错误" });
  }
}
