import assert from "node:assert/strict";
import { test } from "node:test";
import { toolQualitySteps, toolStepEnd, toolStepLabel, type ToolTraceEntry } from "./labels.js";

test("update_resume renders the changed sections and item count", () => {
  assert.equal(toolStepLabel("update_resume", { changes: [{ section: "work" }] }), "修改工作经历");
  assert.equal(
    toolStepLabel("update_resume", { changes: [{ section: "work", items: [{ id: "work_1" }, { id: "work_2" }] }] }),
    "修改工作经历（2 条）",
  );
  assert.equal(
    toolStepLabel("update_resume", { changes: [{ section: "work" }, { section: "projects" }, { section: "skills" }, { section: "awards" }] }),
    "修改工作经历、项目经历、专业技能、…",
  );
});

test("update_resume marks a library import as such", () => {
  assert.equal(toolStepLabel("update_resume", { from_library: [3, 9] }), "从信息库导入 2 条");
  assert.equal(toolStepLabel("update_resume", {}), "修改简历");
});

test("ask_user / library / skill steps read naturally", () => {
  assert.equal(toolStepLabel("ask_user", { questions: [{ question: "a" }, { question: "b" }] }), "向你确认 2 件事");
  assert.equal(toolStepLabel("search_library", { query: "VitaFlow" }), "检索信息库「VitaFlow」");
  assert.equal(toolStepLabel("search_library", {}), "检索信息库");
  assert.equal(toolStepLabel("save_library", { items: [{}, {}] }), "整理 2 条信息到信息库");
  assert.equal(toolStepLabel("use_skill", { name: "JD匹配" }), "使用技能「JD匹配」");
});

test("web_search echoes the clamped query and result count", () => {
  assert.equal(toolStepLabel("web_search", { query: "前端 薪资" }), "联网搜索「前端薪资」");
  const long = toolStepLabel("web_search", { query: "行业惯例 岗位通用要求 目标公司背景 薪资范围 补充说明文字" });
  assert.match(long, /^联网搜索「.+…」$/);
  assert.equal(toolStepLabel("web_search", {}), "联网搜索");
  const end = toolStepEnd("web_search", { content: [{ type: "text", text: "已检索…" }], details: { ok: true, count: 3 } }, false);
  assert.equal(end.detail, "已检索到 3 条结果");
});

test("unknown tools fall back to their raw name; missing args are tolerated", () => {
  assert.equal(toolStepLabel("some_future_tool", {}), "some_future_tool");
  assert.equal(toolStepLabel("ask_user", undefined), "向你确认信息");
});

test("toolStepEnd treats business failure (details.ok=false) as failed with reason", () => {
  const outcome = toolStepEnd(
    "update_resume",
    { content: [{ type: "text", text: "这次修改被拦下了：没有实际内容变化" }], details: { ok: false, reason: "没有实际内容变化" } },
    false,
  );
  assert.equal(outcome.ok, false);
  assert.equal(outcome.detail, "没有实际内容变化");
});

test("toolStepEnd reports thrown tool errors with the message text", () => {
  const outcome = toolStepEnd("search_library", { content: [{ type: "text", text: "信息库暂时读不到（boom）" }], details: {} }, true);
  assert.equal(outcome.ok, false);
  assert.match(outcome.detail ?? "", /^信息库暂时读不到/);
});

test("toolStepEnd summarizes mutation and lookup outcomes without raw payloads", () => {
  const changed = toolStepEnd(
    "update_resume",
    { content: [{ type: "text", text: "已生成待确认修改：已修改：工作经历" }], details: { ok: true, suggestions: ["已修改：工作经历（1 条变为 1 条：A 公司）"] } },
    false,
  );
  assert.equal(changed.detail, "已修改：工作经历（1 条变为 1 条：A 公司）");

  const found = toolStepEnd("search_library", { content: [{ type: "text", text: "[...]" }], details: { ok: true, items: [{}, {}] } }, false);
  assert.equal(found.detail, "找到 2 条相关信息");

  const empty = toolStepEnd("search_library", { content: [{ type: "text", text: "没有" }], details: { ok: true, items: [] } }, false);
  assert.equal(empty.detail, "没有匹配的信息");

  const saved = toolStepEnd("save_library", { content: [{ type: "text", text: "已整理" }], details: { ok: true, pending_info_fragments: [{}] } }, false);
  assert.equal(saved.detail, "整理出 1 条待确认信息");
});

test("toolQualitySteps keeps only settled steps and strips content-bearing fields", () => {
  const trace: ToolTraceEntry[] = [
    { id: "tc1", name: "update_resume", label: "修改工作经历", status: "start" },
    { id: "tc2", name: "update_resume", label: "修改工作经历", status: "end", ok: true, detail: "负责订单服务…", duration_ms: 45 },
    { id: "tc3", name: "save_library", label: "整理", status: "end", ok: false, detail: "数字缺少依据", category: "validation", duration_ms: 120 },
    { id: "tc4", name: "use_skill", label: "使用技能", status: "end", ok: true },
  ];
  const steps = toolQualitySteps(trace);

  // start 行不进摘要；内容字段（label/detail/id）必须被剥掉——它们可能引用简历
  // 内容，要随消息删除，而摘要要长存于 AiTask 事实行。唯一例外：失败步骤的
  // detail 以截断 reason 的身份保留（校验结论文案，不含简历原文）。
  assert.deepEqual(steps, [
    { name: "update_resume", status: "end", ok: true, duration_ms: 45 },
    { name: "save_library", status: "end", ok: false, category: "validation", duration_ms: 120, reason: "数字缺少依据" },
    { name: "use_skill", status: "end", ok: true },
  ]);

  assert.deepEqual(toolQualitySteps([]), []);
});

test("toolQualitySteps caps at 50 steps — auxiliary data must not 422 the billing report", () => {
  const trace: ToolTraceEntry[] = Array.from({ length: 60 }, (_, index) => ({
    id: `tc${index}`,
    name: "update_resume",
    label: "修改",
    status: "end" as const,
    ok: true,
  }));
  assert.equal(toolQualitySteps(trace).length, 50);
});

test("toolQualitySteps keeps the failure reason only on failed steps, clamped to 40 chars", () => {
  const trace: ToolTraceEntry[] = [
    { id: "tc1", name: "update_resume", label: "修改", status: "end", ok: false, detail: "数字缺少依据", category: "validation" },
    // 成功步骤的 detail 是变更摘要（可能回显简历内容），必须留在消息表
    { id: "tc2", name: "update_resume", label: "修改", status: "end", ok: true, detail: "已修改：工作经历（A 公司）" },
    { id: "tc3", name: "web_search", label: "联网搜索", status: "end", ok: false },
  ];
  const [failed, success, failedNoDetail] = toolQualitySteps(trace);
  assert.equal(failed.reason, "数字缺少依据");
  assert.equal(success.reason, undefined);
  assert.equal("reason" in failedNoDetail, false);

  const long = toolQualitySteps([{ id: "tc4", name: "update_resume", label: "修改", status: "end", ok: false, detail: "原".repeat(45) }])[0];
  assert.equal(long.reason?.length, 41, "40 chars plus the ellipsis");
  assert.ok(long.reason?.endsWith("…"));
});
