/**
 * 错误文案的统一口径。
 *
 * 后端返回的中文提示是给人看的，原样透传；axios / fetch 的英文传输层报错
 * （`Network Error`、`Failed to fetch`）对用户没有意义，换成调用方给的兜底
 * 文案。之前 WorkspaceInfo / WorkspaceSettings 各写了一份，语义还不一样。
 */
export function errorMessage(error: unknown, fallback: string): string {
  const raw = error instanceof Error ? error.message : ""
  if (!raw) return fallback
  return /[\u4e00-\u9fa5]/.test(raw) ? raw : fallback
}

/** 用户主动取消（切简历、关面板）不该当成失败提示。 */
export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError"
}
