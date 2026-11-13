/**
 * Unit tests for ask_user: it records the curated question list into per-turn
 * state (echoed onto the result event as pending_questions) and caps the list.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createAskUserTool, MAX_ASKED_QUESTIONS } from "./questions.js";
import { createTurnState } from "./state.js";

describe("ask_user tool", () => {
  it("records trimmed questions with hints into per-turn state", async () => {
    const state = createTurnState();
    const tool = createAskUserTool(state);
    const result = await tool.execute(
      "call_1",
      {
        questions: [
          { question: "  是否有百万级 DAU 的项目经历？ ", hint: " 例如：在 XX 公司负责 XX 产品 " },
          { question: " 是否持有云原生认证？", hint: "  " },
        ],
      } as never,
      undefined as never,
      undefined as never,
      undefined as never,
    );
    assert.equal(result.details.ok, true);
    assert.deepEqual(state.askedQuestions, [
      { question: "是否有百万级 DAU 的项目经历？", hint: "例如：在 XX 公司负责 XX 产品" },
      { question: "是否持有云原生认证？" },
    ]);
  });

  it("drops blank questions and caps the list", async () => {
    const state = createTurnState();
    const tool = createAskUserTool(state);
    const raw = Array.from({ length: MAX_ASKED_QUESTIONS + 2 }, (_, i) => ({ question: `问题 ${i + 1}` }));
    raw.unshift({ question: "   " });
    const result = await tool.execute("call_2", { questions: raw } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(result.details.ok, true);
    assert.equal(state.askedQuestions?.length, MAX_ASKED_QUESTIONS);
    assert.equal(MAX_ASKED_QUESTIONS, 3, "three is the cap: more than that gets skipped");
  });

  it("fails without recording when no valid question is given", async () => {
    const state = createTurnState();
    const tool = createAskUserTool(state);
    const result = await tool.execute("call_3", { questions: [{ question: "" }, {}] } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(result.details.ok, false);
    assert.equal(state.askedQuestions, null);
  });
});
