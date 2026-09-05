/**
 * Structured gap-question tool for the /JD优化 flow (and any future
 * information-collection turn): when the model finds resume-vs-JD gaps it cannot
 * fill from evidence, it submits its questions here instead of free-text asking.
 *
 * The questions ride the result event (pending_questions) so the backend persists
 * them on the assistant message and the frontend renders its step-by-step
 * answer dialog — chat text alone cannot be turned into a form reliably.
 */
import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";

/** One curated question shown in the frontend's answer dialog. */
export interface AskedQuestion {
  question: string;
  hint?: string;
}

/** Per-turn state so agent.ts can echo the questions onto the result event. */
export interface QuestionsToolState {
  askedQuestions: AskedQuestion[] | null;
}

export function createQuestionsState(): QuestionsToolState {
  return { askedQuestions: null };
}

/** Hard cap shared with the /JD优化 template guidance — keep the dialog finishable. */
export const MAX_ASKED_QUESTIONS = 5;

export function createAskUserQuestionsTool(state: QuestionsToolState) {
  return defineTool({
    name: "ask_user_questions",
    label: "收集补充信息",
    description:
      "当简历与岗位/需求差距较大、缺少可写入的证据时，把需要向用户核实的问题整理成结构化清单一次性提交。" +
      "用户会在弹出的补充信息框里逐条回答后回传。只在确有信息缺口时使用；没有缺口不要调用。",
    promptSnippet: "ask_user_questions(questions) — 向用户收集补充信息",
    promptGuidelines: [
      "问题必须与已发现的证据缺口一一对应，一次不超过 5 个。",
      "hint 是给用户的填写示例或说明，帮助其理解要提供什么；没有合适的就不传。",
      "调用后立即结束回合等待用户回答，不要在同一回合继续生成修改方案。",
    ],
    parameters: Type.Object({
      questions: Type.Array(
        Type.Object({
          question: Type.String({ description: "要向用户核实的问题" }),
          hint: Type.Optional(Type.String({ description: "给用户的填写示例或说明" })),
        }),
        { description: "问题清单" },
      ),
    }),
    execute: async (
      _toolCallId,
      params: { questions?: Array<{ question?: string; hint?: string }> },
      _signal,
      _onUpdate,
      _ctx,
    ): Promise<{ content: Array<{ type: "text"; text: string }>; details: { ok: boolean; questions?: AskedQuestion[] } }> => {
      const raw = Array.isArray(params?.questions) ? params.questions : [];
      const questions: AskedQuestion[] = raw
        .map((item) => {
          const question = String(item?.question ?? "").trim();
          const hint = String(item?.hint ?? "").trim();
          return hint ? { question, hint } : { question };
        })
        .filter((item) => item.question)
        .slice(0, MAX_ASKED_QUESTIONS);
      if (!questions.length) {
        return {
          content: [{ type: "text", text: "没有有效的问题。请给出具体要向用户核实的问题。" }],
          details: { ok: false },
        };
      }
      state.askedQuestions = questions;
      return {
        content: [{ type: "text", text: `已向用户展示 ${questions.length} 个补充问题。请结束回合，等待用户作答后再继续。` }],
        details: { ok: true, questions },
      };
    },
  });
}
