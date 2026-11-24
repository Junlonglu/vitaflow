/**
 * BYOK 出站目标守卫（RF-012）。
 *
 * 用户填的 `base_url` 是不可信输入。保存时后端只拦「字面量是内网 IP」，
 * 拦不住「域名解析到内网」——那正是 DNS rebinding：`attacker.example` 第一次
 * 解析到公网、真正拨号时解析到 127.0.0.1 或 169.254.169.254，AI 服务就成了
 * 一个能读内网、并把响应塞进模型上下文的代理。
 *
 * 三层收口：
 *
 * 1. `assertPublicTarget`：每轮注册 BYOK 模型前解析一次，命中内网立即报错，
 *    用户看到的是一句人话而不是「连接超时」；
 * 2. `guardHost` + `dns-lookup`：登记过的 BYOK 主机在**建立连接时**再查一遍，
 *    这一次用的是真正拨号用的那次解析结果，所以 rebinding 也没有第二次机会；
 * 3. `installOutboundFetchGuard`：给登记过的主机强制 `redirect: "error"`，
 *    公网主机不能用 302 把请求跳到内网（重定向目标不会再走上面的解析检查）。
 *
 * **不拦平台自己的 provider**：自托管模型跑在 192.168.x / 127.0.0.1 是正常部署，
 * 拦了就等于不支持企业内网模型。这条线只对「用户在浏览器里填的地址」生效。
 *
 * **已知豁免**：`localhost` / `*.local` / `*.internal` / `*.lan` 走系统解析器
 * （mDNS、企业内网 DNS），不经过第 2 层的地址过滤——这类名字本来就解析不出
 * 公网地址。它们由第 1 层（每轮注册前解析并拒绝内网）兜住。
 */
import { promises as dnsPromises } from "node:dns";

/** 已登记的 BYOK 主机（小写、不含端口）。全局集合：解析层与 fetch 层共用。 */
const guardedHosts = new Set<string>();

const IPV4_RE = /^\d{1,3}(?:\.\d{1,3}){3}$/;

/** 私有 / 保留 / 元数据地址。只判「不能去的」，不判「能去的」。 */
export function isPrivateAddress(address: string): boolean {
  const raw = (address || "").trim().toLowerCase().replace(/^\[|\]$/g, "");
  if (!raw) return true;
  if (IPV4_RE.test(raw)) {
    const [a, b] = raw.split(".").map((part) => Number(part));
    if (a === 0 || a === 10 || a === 127) return true;
    if (a === 169 && b === 254) return true; // link-local，含云元数据 169.254.169.254
    if (a === 172 && b >= 16 && b <= 31) return true;
    if (a === 192 && b === 168) return true;
    if (a === 100 && b >= 64 && b <= 127) return true; // CGNAT
    if (a >= 224) return true; // 组播 + 保留
    return false;
  }
  // IPv6：回环 ::1、链路本地 fe80::/10、唯一本地 fc00::/7、IPv4 映射 ::ffff:a.b.c.d
  if (raw === "::" || raw === "::1") return true;
  if (raw.startsWith("fe8") || raw.startsWith("fe9") || raw.startsWith("fea") || raw.startsWith("feb")) return true;
  if (raw.startsWith("fc") || raw.startsWith("fd")) return true;
  if (raw.startsWith("::ffff:")) return isPrivateAddress(raw.slice(7));
  return false;
}

/** 解析层拒绝连接时抛这个，而不是普通 Error：调用方要能区分「策略拒绝」与「解析失败」。 */
export class BlockedTargetError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BlockedTargetError";
  }
}

export function guardHost(hostname: string): void {
  const name = (hostname || "").trim().toLowerCase();
  if (name) guardedHosts.add(name);
}

export function isGuardedHost(hostname: string): boolean {
  return guardedHosts.has((hostname || "").trim().toLowerCase());
}

/** 测试用：清空登记，避免用例之间互相影响。 */
export function resetGuardedHosts(): void {
  guardedHosts.clear();
}

export interface TargetCheck {
  ok: boolean;
  host: string;
  addresses: string[];
  error?: string;
}

/** 可注入的解析器：单测不该依赖真实 DNS，也不该为了跑测试去连网。 */
let resolveTarget: (hostname: string) => Promise<string[]> = defaultResolve;

export function setTargetResolver(fn: (hostname: string) => Promise<string[]>) {
  resolveTarget = fn;
}

export function resetTargetResolver() {
  resolveTarget = defaultResolve;
}

async function defaultResolve(hostname: string): Promise<string[]> {
  if (IPV4_RE.test(hostname) || hostname.includes(":")) return [hostname];
  // 绕过被替换的 dns.lookup：这里要的是「真实解析结果」，而不是过滤后的。
  const results = await Promise.allSettled([
    dnsPromises.resolve4(hostname),
    dnsPromises.resolve6(hostname),
  ]);
  const addresses: string[] = [];
  for (const result of results) {
    if (result.status === "fulfilled") addresses.push(...result.value);
  }
  if (!addresses.length) {
    // 解析不出来时交回系统解析器（`localhost`、容器别名等），拿到什么判什么。
    const looked = await dnsPromises.lookup(hostname, { all: true }).catch(() => [] as { address: string }[]);
    addresses.push(...looked.map((item) => item.address));
  }
  return addresses;
}

/**
 * 校验一个 URL 是否指向公网。`allowPrivate=false` 用于 BYOK 地址。
 *
 * 只要**任一**解析结果落在内网就拒绝：DNS 轮询返回公私混杂时，攻击者总能让
 * 客户端选中那个内网地址。
 */
export async function assertPublicTarget(rawUrl: string): Promise<TargetCheck> {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return { ok: false, host: "", addresses: [], error: "接入地址不是合法的 URL" };
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { ok: false, host: url.hostname, addresses: [], error: "接入地址必须以 http(s):// 开头" };
  }
  const host = url.hostname.replace(/^\[|\]$/g, "");
  if (host === "localhost" || host.endsWith(".localhost")) {
    return { ok: false, host, addresses: [], error: "接入地址不能是 localhost" };
  }
  const addresses = await resolveTarget(host);
  if (!addresses.length) {
    return { ok: false, host, addresses, error: `接入地址的域名无法解析：${host}` };
  }
  const bad = addresses.find((address) => isPrivateAddress(address));
  if (bad) {
    return {
      ok: false,
      host,
      addresses,
      error: `接入地址指向内网（${bad}），自定义模型不能访问私有网络`,
    };
  }
  guardHost(host);
  return { ok: true, host, addresses };
}

let installed = false;

/**
 * 只计算「这次请求要不要被改写成 redirect:error」，不真的发请求。
 *
 * 单独暴露出来是为了可测：`installOutboundFetchGuard` 只能装一次，测试没法
 * 反复重装去观察行为。
 */
export function redirectGuardInit(input: unknown, init?: RequestInit): RequestInit | undefined {
  try {
    const raw =
      typeof input === "string" ? input : input instanceof URL ? input.toString() : (input as { url?: string })?.url ?? "";
    const host = new URL(raw).hostname.replace(/^\[|\]$/g, "").toLowerCase();
    if (guardedHosts.has(host) && (!init || init.redirect === undefined)) {
      return { ...(init ?? {}), redirect: "error" };
    }
  } catch {
    // URL 解析失败交给原始的 fetch 去报错，这里不改变行为。
  }
  return init;
}

/**
 * 给登记过的 BYOK 主机强制 `redirect: "error"`。
 *
 * 不这么做的话：公网主机返回 302 → 目标变成内网地址 → 这一次跳转既不过解析
 * 检查（不是域名的解析），也不在票据范围内。禁掉重定向是最省事也最彻底的解。
 */
export function installOutboundFetchGuard(): void {
  if (installed) return;
  installed = true;
  const original = globalThis.fetch;
  globalThis.fetch = ((input, init) => original(input, redirectGuardInit(input, init))) as typeof globalThis.fetch;
}
