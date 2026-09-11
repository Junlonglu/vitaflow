import assert from "node:assert/strict";
import { test } from "node:test";
import { toolStepEnd, toolStepLabel } from "./labels.js";

test("get_resume_context maps section keys to Chinese labels", () => {
  assert.equal(toolStepLabel("get_resume_context", { sections: "work,projects" }), "查阅简历「实习/工作经历、项目经历」");
  assert.equal(toolStepLabel("get_resume_context", { sections: ["skills"] }), "查阅简历「专业技能」");
  // Models often pass a JSON-encoded array string; the label must unwrap it.
  assert.equal(toolStepLabel("get_resume_context", { sections: '["skills", "awards"]' }), "查阅简历「专业技能、荣誉奖项」");
});

test("get_resume_context clamps unknown section keys and long lists", () => {
  assert.equal(toolStepLabel("get_resume_context", { sections: "work,projects,skills,awards" }), "查阅简历「实习/工作经历、项目经历、专业技能、…」");
  assert.equal(toolStepLabel("get_resume_context", { sections: "totally_unknown_key" }), "查阅简历「totally_unkn…」");
});

test("update_section and propose_change render their scope", () => {
  assert.equal(toolStepLabel("update_section", { section: "work" }), "修改「实习/工作经历」模块");
  assert.equal(
    toolStepLabel("propose_change", { updates: [{ section: "work" }, { section: "projects" }] }),
    "生成修改提案（实习/工作经历、项目经历）",
  );
});

test("update_basics_field lists the changed field labels", () => {
  assert.equal(toolStepLabel("update_basics_field", { basics: { location: "深圳", title: "后端" } }), "修改基本信息（所在城市、求职方向）");
  assert.equal(toolStepLabel("update_basics_field", { basics: {} }), "修改基本信息");
});

test("web_search echoes the clamped query and result count", () => {
  assert.equal(toolStepLabel("web_search", { query: "前端 薪资" }), "联网搜索「前端薪资」");
  const long = toolStepLabel("web_search", { query: "行业惯例 岗位通用要求 目标公司背景 薪资范围 补充说明文字" });
  assert.match(long, /^联网搜索「.+…」$/);
  assert.equal(toolStepLabel("web_search", {}), "联网搜索");
  const end = toolStepEnd("web_search", { content: [{ type: "text", text: "已检索…" }], details: { ok: true, count: 3 } }, false);
  assert.equal(end.ok, true);
  assert.equal(end.detail, "已检索到 3 条结果");
  const empty = toolStepEnd("web_search", { content: [{ type: "text", text: "未检索到…" }], details: { ok: true, count: 0 } }, false);
  assert.equal(empty.detail, "未检索到相关结果");
});

test("unknown tools fall back to their raw name; missing args are tolerated", () => {
  assert.equal(toolStepLabel("some_future_tool", {}), "some_future_tool");
  assert.equal(toolStepLabel("update_section", undefined), "修改「」模块");
});

test("toolStepEnd treats business failure (details.ok=false) as failed with reason", () => {
  const outcome = toolStepEnd(
    "update_section",
    { content: [{ type: "text", text: "该修改未通过校验，已阻止写入。原因：没有实际内容变化" }], details: { ok: false, reason: "没有实际内容变化" } },
    false,
  );
  assert.equal(outcome.ok, false);
  assert.equal(outcome.detail, "没有实际内容变化");
});

test("toolStepEnd reports thrown tool errors with the message text", () => {
  const outcome = toolStepEnd("get_resume_context", { content: [{ type: "text", text: "读取简历模块失败：无法连接后端服务（boom）" }], details: {} }, true);
  assert.equal(outcome.ok, false);
  assert.match(outcome.detail ?? "", /^读取简历模块失败：无法连接后端服务（boom）/);
});

test("toolStepEnd summarizes read and mutation outcomes without raw payloads", () => {
  const read = toolStepEnd("get_resume_context", { content: [{ type: "text", text: '看似简历正文（不得外传）' }], details: { ok: true, sections: { work: [], projects: [] } } }, false);
  assert.equal(read.ok, true);
  assert.equal(read.detail, "已读取 2 个模块");

  const propose = toolStepEnd("propose_change", { content: [{ type: "text", text: "已生成待确认修改：已修改：实习/工作经历" }], details: { ok: true, suggestions: ["已修改：实习/工作经历（2 条变为 2 条：A 公司）"] } }, false);
  assert.equal(propose.ok, true);
  assert.equal(propose.detail, "已修改：实习/工作经历（2 条变为 2 条：A 公司）");

  const emptyRead = toolStepEnd("get_resume_context", { content: [{ type: "text", text: "未读取到该模块。" }], details: { ok: true, sections: {} } }, false);
  assert.equal(emptyRead.detail, "该模块暂无内容");
});
