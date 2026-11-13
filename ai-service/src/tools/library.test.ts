/**
 * search_library / save_library：信息库的两个方向——只读检索与待确认沉淀。
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createLibraryTools } from "./library.js";
import { createTurnState } from "./state.js";

function buildTools(respond: (call: { url: string; body?: any }) => Response | Promise<Response>) {
  const calls: Array<{ url: string; body?: any }> = [];
  const state = createTurnState();
  const [search, save] = createLibraryTools({
    fetch: (async (url: string, init?: RequestInit) => {
      const call = { url: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(call);
      return respond(call);
    }) as typeof fetch,
    fastapiBaseUrl: "http://api.test/",
    serviceToken: "token",
    userId: 7,
    resumeId: 9,
    state,
  });
  return { search, save, calls, state };
}

const json = (data: unknown) =>
  new Response(JSON.stringify({ data }), { status: 200, headers: { "content-type": "application/json" } });

describe("search_library", () => {
  it("builds the query URL and returns the cards", async () => {
    const { search, calls } = buildTools(() => json({ ok: true, items: [{ id: 3, title: "VitaFlow 项目" }] }));
    const result: any = await search.execute(
      "c1",
      { query: "VitaFlow", kind: "project", limit: 4 } as never,
      undefined as never,
      undefined as never,
      undefined as never,
    );
    assert.equal(
      calls[0].url,
      "http://api.test/api/ai/service/tool/info-fragments/search?user_id=7&query=VitaFlow&kinds=project&limit=4",
    );
    assert.equal(result.details.ok, true);
    assert.match(String(result.content[0].text), /VitaFlow 项目/);
  });

  it("clamps the limit and tolerates an empty query", async () => {
    const { search, calls } = buildTools(() => json({ ok: true, items: [] }));
    await search.execute("c2", { limit: 99 } as never, undefined as never, undefined as never, undefined as never);
    assert.match(calls[0].url, /query=&kinds=&limit=20$/);
  });

  it("tells the model there is nothing rather than inventing hits", async () => {
    const { search } = buildTools(() => json({ ok: true, items: [] }));
    const result: any = await search.execute("c3", { query: "不存在" } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(result.details.ok, true);
    assert.deepEqual(result.details.items, []);
    assert.match(String(result.content[0].text), /没有匹配的条目/);
  });
});

describe("save_library", () => {
  it("posts the items and records the pending fragments", async () => {
    const { save, calls, state } = buildTools(() =>
      json({ ok: true, pending_info_fragments: [{ kind: "project", title: "巡检项目" }], issues: [] }),
    );
    const result: any = await save.execute(
      "c4",
      { items: [{ payload: { name: "巡检项目", role: "负责人" } }] } as never,
      undefined as never,
      undefined as never,
      undefined as never,
    );
    assert.equal(calls[0].url, "http://api.test/api/ai/service/tool/info-fragments/propose");
    assert.deepEqual(calls[0].body, {
      user_id: 7,
      resume_id: 9,
      fragments: [{ payload: { name: "巡检项目", role: "负责人" } }],
    });
    assert.equal(state.pendingFragments?.length, 1);
    assert.match(String(result.content[0].text), /确认后才会存入信息库/);
  });

  it("reports validation issues from the backend", async () => {
    const { save } = buildTools(() => json({ ok: false, reason: "第 1 条：除标题外没有其它内容", category: "validation" }));
    const result: any = await save.execute(
      "c5",
      { items: [{ payload: {} }] } as never,
      undefined as never,
      undefined as never,
      undefined as never,
    );
    assert.equal(result.details.ok, false);
    assert.match(String(result.content[0].text), /除标题外没有其它内容/);
  });

  it("refuses an empty item list without calling the backend", async () => {
    const { save, calls } = buildTools(() => json({ ok: true, pending_info_fragments: [] }));
    const result: any = await save.execute("c6", { items: [] } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(calls.length, 0);
    assert.equal(result.details.ok, false);
  });
});
