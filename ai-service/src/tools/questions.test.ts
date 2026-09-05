/**
 * Unit tests for the ask_user_questions tool: validates the curated question
 * list it records into per-turn state (echoed onto the result event as
 * pending_questions) and its empty/oversized input handling.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createAskUserQuestionsTool, createQuestionsState, MAX_ASKED_QUESTIONS } from "./questions.js";

describe("ask_user_questions tool", () => {
  it("records trimmed questions with hints into per-turn state", async () => {
    const state = createQuestionsState();
    const tool = createAskUserQuestionsTool(state);
    const result = await tool.execute("call_1", {
      questions: [
        { question: "  是否有百万级 DAU 的项目经历？ ", hint: " 例如：在 XX 公司负责 XX 产品 " },
        { question: " 是否持有云原生认证？", hint: "  " },
      ],
    } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(result.details.ok, true);
    assert.deepEqual(state.askedQuestions, [
      { question: "是否有百万级 DAU 的项目经历？", hint: "例如：在 XX 公司负责 XX 产品" },
      { question: "是否持有云原生认证？" },
    ]);
  });

  it("drops blank questions and caps the list", async () => {
    const state = createQuestionsState();
    const tool = createAskUserQuestionsTool(state);
    const raw = Array.from({ length: MAX_ASKED_QUESTIONS + 2 }, (_, i) => ({ question: `问题 ${i + 1}` }));
    raw.unshift({ question: "   " });
    const result = await tool.execute("call_2", { questions: raw } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(result.details.ok, true);
    assert.equal(state.askedQuestions?.length, MAX_ASKED_QUESTIONS);
  });

  it("fails without recording when no valid question is given", async () => {
    const state = createQuestionsState();
    const tool = createAskUserQuestionsTool(state);
    const result = await tool.execute("call_3", { questions: [{ question: "" }, {}] } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(result.details.ok, false);
    assert.equal(state.askedQuestions, null);
  });
});
