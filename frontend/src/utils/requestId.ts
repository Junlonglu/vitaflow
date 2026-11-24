/**
 * 幂等键：一次用户动作一个，失败重试复用同一个，成功后作废。
 *
 * 后端把 `request_id` 写进唯一约束：同一个键的第二次请求不会建出第二行，
 * 而是回放第一次的结果。所以这里的生命周期必须是「动作级」而不是「请求级」——
 * 每次发请求都重新生成等于没有幂等键。
 */
export function newRequestId(): string {
  const cryptoRef = globalThis.crypto as Crypto | undefined
  if (cryptoRef && typeof cryptoRef.randomUUID === "function") return cryptoRef.randomUUID()
  // 老浏览器兜底：只要在本机唯一即可，服务端只做等价比较。
  return `r${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}
