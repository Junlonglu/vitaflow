/**
 * Per-turn tool state.
 *
 * 工具不直接写库：它们把「这一轮产生了什么」记在这里，由 agent.ts 在 result
 * 事件里一并回传，FastAPI 再落成待确认消息。三块内容互相独立，可以同时存在——
 * 一次回复里既改了简历，又整理了信息库条目，是正常情况。
 */

export interface PendingChange {
  optimized_resume_data: unknown;
  suggestions: string[];
}

export interface AskedQuestion {
  question: string;
  hint?: string;
}

export interface TurnState {
  /** 待确认的简历修改（update_resume 产出）。 */
  pendingChange: PendingChange | null;
  /** 待确认入库的信息条目（save_library 产出）。 */
  pendingFragments: Array<Record<string, unknown>> | null;
  /** 需要用户回答的结构化问题（ask_user 产出）。 */
  askedQuestions: AskedQuestion[] | null;
}

export function createTurnState(): TurnState {
  return { pendingChange: null, pendingFragments: null, askedQuestions: null };
}
