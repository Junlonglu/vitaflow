/**
 * VitaFlow AI service HTTP entry point.
 *
 * A thin, single-purpose sidecar: it authenticates the FastAPI proxy with a
 * static token, runs one pi-agent session per request, and streams NDJSON
 * events back. It holds no state and can be scaled horizontally.
 */
import "dotenv/config";
import { timingSafeEqual } from "node:crypto";
import express from "express";
import { runChat, type ChatStreamEvent } from "./agent.js";
import { loadConfig } from "./config.js";
import { createFauxRuntime, type FauxRuntime } from "./faux.js";
import { buildContextPrompt, DEFAULT_SYSTEM_PROMPT, type AiServiceContext } from "./prompt.js";

const config = loadConfig();
const app = express();
app.use(express.json());

const TOKEN_HEADERS = ["authorization", "x-ai-service-token"] as const;

function tokenMatches(provided: string | undefined): boolean {
  if (!config.serviceToken) return false;
  if (!provided) return false;
  const a = Buffer.from(provided.trim());
  const b = Buffer.from(config.serviceToken.trim());
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

function authorize(req: express.Request): boolean {
  for (const header of TOKEN_HEADERS) {
    const value = req.headers[header];
    if (typeof value === "string") {
      const candidate = header === "authorization" && value.toLowerCase().startsWith("bearer ")
        ? value.slice("bearer ".length)
        : value;
      if (tokenMatches(candidate)) return true;
    }
  }
  return false;
}

function sendEvent(res: express.Response, event: ChatStreamEvent): void {
  if (res.writableEnded) return;
  res.write(`${JSON.stringify(event)}\n`);
}

app.get("/health", (_req, res) => {
  res.json({ status: "ok", provider: config.provider, ready: config.provider === "faux" });
});

app.post("/v1/chat", async (req, res) => {
  if (!authorize(req)) {
    res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
    return;
  }
  if (config.provider !== "faux") {
    res.status(501).json({ type: "error", message: "当前骨架仅支持 faux 模式，真实 Provider 尚未接入" });
    return;
  }

  const message = typeof req.body?.message === "string" ? req.body.message.trim() : "";
  if (!message) {
    res.status(400).json({ type: "error", message: "message 不能为空" });
    return;
  }
  const context: AiServiceContext | null =
    typeof req.body?.context === "object" && req.body.context !== null ? req.body.context : null;
  const userId = Number.isInteger(req.body?.user_id) ? req.body.user_id : 0;
  const resumeId = Number.isInteger(req.body?.resume_id) ? req.body.resume_id : 0;
  const systemPrompt = typeof req.body?.system_prompt === "string" && req.body.system_prompt.trim()
    ? req.body.system_prompt
    : buildContextPrompt(DEFAULT_SYSTEM_PROMPT, context);

  res.status(200);
  res.setHeader("Content-Type", "application/x-ndjson; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  const controller = new AbortController();
  res.on("close", () => controller.abort());

  await runChat(
    { message, systemPrompt, config, runtime, userId, resumeId },
    (event) => sendEvent(res, event),
    controller.signal,
  );
  res.end();
});

let runtime: FauxRuntime;

async function main() {
  runtime = await createFauxRuntime();
  app.listen(config.port, () => {
    console.log(`[vitaflow-ai] listening on :${config.port} provider=${config.provider}`);
  });
}

main().catch((err) => {
  console.error("[vitaflow-ai] failed to start:", err);
  process.exit(1);
});
