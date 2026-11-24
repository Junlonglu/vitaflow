/**
 * 错误出口的回归测试（RF-010）。
 *
 * 触发条件很朴素：任何 async handler 里一个 await 抛错。修复前的表现是进程
 * 直接退出（Node 22 把未处理的拒绝当致命错误），修复后必须是「这一个请求
 * 失败，其它回合照常」。
 */
import assert from "node:assert/strict";
import test from "node:test";
import { runHandler } from "./httpErrors.js";

interface FakeResponse {
  headersSent: boolean;
  statusCode: number | null;
  body: unknown;
  written: string;
  ended: number;
  writableEnded: boolean;
  status(code: number): FakeResponse;
  json(payload: unknown): FakeResponse;
  write(chunk: string): boolean;
  end(): void;
}

function fakeResponse(headersSent = false): FakeResponse {
  const res: FakeResponse = {
    headersSent,
    statusCode: null,
    body: undefined,
    written: "",
    ended: 0,
    writableEnded: false,
    status(code) {
      res.statusCode = code;
      return res;
    },
    json(payload) {
      res.body = payload;
      return res;
    },
    write(chunk) {
      res.written += chunk;
      return true;
    },
    end() {
      res.ended += 1;
      res.writableEnded = true;
    },
  };
  return res;
}

test("handler 在写响应前抛错：返回 500 JSON，客户端能解析", async () => {
  const res = fakeResponse();
  await runHandler("chat", res as never, async () => {
    throw new Error("boom before flush");
  });

  assert.equal(res.statusCode, 500);
  assert.deepEqual(res.body, { type: "error", message: "AI 服务内部错误" });
  assert.equal(res.ended, 0, "json() 自己收尾，不需要再 end");
});

test("handler 在流式阶段抛错：补 error 事件并收尾，不换状态码", async () => {
  const res = fakeResponse(true);
  await runHandler("chat", res as never, async () => {
    throw new Error("boom mid-stream");
  });

  assert.equal(res.statusCode, null, "已经发出的响应头不能再改");
  assert.equal(res.body, undefined, "不能再写 JSON 体");
  assert.match(res.written, /"type":"error"/);
  assert.equal(res.ended, 1, "必须收尾，否则客户端一直挂着");
});

test("handler 正常结束时不写任何额外内容", async () => {
  const res = fakeResponse();
  let ran = 0;
  await runHandler("task", res as never, async () => {
    ran += 1;
  });

  assert.equal(ran, 1);
  assert.equal(res.statusCode, null);
  assert.equal(res.written, "");
  assert.equal(res.ended, 0);
});
