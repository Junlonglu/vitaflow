/**
 * ask_user — 把「简历里没有、又必须问清楚」的信息整理成结构化问题。
 *
 * 为什么不是直接在回复里提问：前端弹出的补充信息框可以逐条回答并原样回传，
 * 模型拿到的是干净的事实，而不是一段自由文本。
 *
 * 限制在 3 个以内：多数简历缺口一次问不完就不该一次问完，先补最关键的那几个。
 */
import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";
import type { AskedQuestion } from "./state.js";
import type { TurnState } from "./state.js";

/** Hard cap — 问题越多，用户越可能直接跳过这一轮。 */
export const MAX_ASKED_QUESTIONS = 3;

export function createAskUserTool(state: TurnState) {
  return defineTool({
    name: "ask_user",
    label: "补充信息",
    description:
      "简历里缺少关键事实（结果数字、职责范围、时间、奖项等级）导致无法下笔时，把要核实的问题一次性提交，" +
      "用户会在补充信息框里逐条回答。没有问题就不要调用。",
    parameters: Type.Object({
      questions: Type.Array(
        Type.Object({
          question: Type.String({ description: "要核实的问题，越具体越好" }),
          hint: Type.Optional(Type.String({ description: "填写示例或说明，帮助用户理解要答什么" })),
        }),
        { description: `问题清单，最多 ${MAX_ASKED_QUESTIONS} 个` },
      ),
    }),
    execute: async (
      _toolCallId,
      params: { questions?: Array<{ question?: string; hint?: string }> },
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
          content: [{ type: "text", text: "没有有效的问题。请给出具体要核实的事实。" }],
          details: { ok: false },
        };
      }
      state.askedQuestions = questions;
      return {
        content: [{ type: "text", text: `已向用户提出 ${questions.length} 个问题。请结束回合，等用户回答后再继续。` }],
        details: { ok: true, questions },
      };
    },
  });
}
