/**
 * Bypass the flaky Docker Desktop DNS path for outbound connections.
 *
 * Symptom: after an idle period the first chat turn stalls 5-13s before the
 * provider's first token. Measured root cause: `dns.lookup` (getaddrinfo →
 * Docker's embedded resolver 127.0.0.11, which forwards to the host resolver)
 * intermittently times out with EAI_AGAIN after 8-18s, while a direct UDP
 * query (`dns.resolve4` → the configured nameservers) answers in ~10ms.
 *
 * Fix: install a process-wide `dns.lookup` that resolves via resolve4/resolve6
 * (the fast path) with a short TTL cache, falling back to the system lookup
 * for Docker-special names, localhost and IP literals. Node's net/undici/OpenAI
 * SDK all resolve through `dns.lookup`, so one install covers every client.
 */
import { lookup as systemLookup, promises as dnsPromises } from "node:dns";
import { createRequire } from "node:module";
import { BlockedTargetError, isGuardedHost, isPrivateAddress } from "./ssrf.js";

const CACHE_TTL_MS = 5 * 60_000;

interface AddressEntry {
  address: string;
  family: number;
}

const cache = new Map<string, { entries: AddressEntry[]; expires: number }>();
const inflight = new Map<string, Promise<AddressEntry[]>>();

const IP_LITERAL_RE = /^(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f:]+:[0-9a-f:]*)(?:%\w+)?$/i;
const SYSTEM_FALLBACK_RE = /^(localhost)|(\.(local|internal|lan)$)/i;

function resolveEntries(hostname: string): Promise<AddressEntry[]> {
  const cached = cache.get(hostname);
  if (cached && cached.expires > Date.now()) return Promise.resolve(cached.entries);
  const pending = inflight.get(hostname);
  if (pending) return pending;
  const promise = (async () => {
    // A 记录是主路径；AAAA 查询经 Docker DNS 转发偶发长时间悬挂（无 AAAA 记录时
    // 上游超时才返回），只给 300ms 宽限，拿不到就不返回 v6——容器内本就少有可用 v6 路由。
    const startedAt = Date.now();
    const v4Promise = dnsPromises.resolve4(hostname);
    const v6Promise = Promise.race([
      dnsPromises.resolve6(hostname).catch(() => [] as string[]),
      new Promise<string[]>((resolve) => setTimeout(() => resolve([]), 300)),
    ]).catch(() => [] as string[]);
    const [v4, v6] = await Promise.all([
      v4Promise.then(
        (addresses) => addresses,
        (err) => {
          console.warn(`[dns] resolve4(${hostname}) failed after ${Date.now() - startedAt}ms:`, (err as NodeJS.ErrnoException)?.code ?? err);
          return [] as string[];
        },
      ),
      v6Promise,
    ]);
    const entries: AddressEntry[] = [
      ...v4.map((address) => ({ address, family: 4 as const })),
      ...v6.map((address) => ({ address, family: 6 as const })),
    ];
    if (!entries.length) throw new Error(`no addresses for ${hostname}`);
    // BYOK 主机（RF-012）：这次解析就是拨号要用的那次，私网地址在这里挡掉，
    // DNS rebinding 就没有「第一次查公网、第二次连内网」的窗口。
    if (isGuardedHost(hostname)) {
      const safe = entries.filter((entry) => !isPrivateAddress(entry.address));
      if (!safe.length) {
        throw new BlockedTargetError(`refusing to connect ${hostname} to a private address`);
      }
      cache.set(hostname, { entries: safe, expires: Date.now() + CACHE_TTL_MS });
      return safe;
    }
    const elapsed = Date.now() - startedAt;
    if (elapsed > 1000) console.warn(`[dns] cold resolve ${hostname} took ${elapsed}ms (v4=${v4.length} v6=${v6.length})`);
    cache.set(hostname, { entries, expires: Date.now() + CACHE_TTL_MS });
    return entries;
  })().finally(() => inflight.delete(hostname));
  inflight.set(hostname, promise);
  return promise;
}

function filterFamily(entries: AddressEntry[], family: number | undefined): AddressEntry[] {
  if (family === 4 || family === 6) return entries.filter((entry) => entry.family === family);
  return entries;
}

type LookupOptions = import("node:dns").LookupOptions;
type LookupCallback = (
  err: NodeJS.ErrnoException | null,
  address: string,
  family: number,
) => void;
type LookupAllCallback = (err: NodeJS.ErrnoException | null, addresses: AddressEntry[]) => void;

export function installCachedLookup(): void {
  // ESM 的命名空间对象不可变；拿到 CJS 的 dns 模块对象才能替换 lookup
  const dns = createRequire(import.meta.url)("node:dns") as typeof import("node:dns");
  if ((dns.lookup as unknown as Record<string, unknown>).__cachedLookup) return;
  const patched = function cachedLookup(hostname: string, options: LookupOptions, callback: LookupCallback | LookupAllCallback): void {
    // 兼容旧式签名 dns.lookup(host, cb) 与 dns.lookup(host, 4, cb)——替换的是
    // 公共 API，调用方未必都传 options 对象。
    let opts = options;
    if (typeof opts === "function") {
      callback = opts as unknown as LookupCallback | LookupAllCallback;
      opts = {} as LookupOptions;
    } else if (typeof opts === "number") {
      opts = { family: opts };
    }
    if (IP_LITERAL_RE.test(hostname) || SYSTEM_FALLBACK_RE.test(hostname)) {
      (systemLookup as unknown as (h: string, o: LookupOptions, cb: unknown) => void)(hostname, opts, callback);
      return;
    }
    resolveEntries(hostname).then(
      (all) => {
        // family 除数字外还有旧式 "IPv4"/"IPv6" 字符串形式，需映射后过滤，
        // 否则请求 v6 的调用方可能拿到 v4 地址
        const wanted = typeof opts?.family === "number"
          ? opts.family
          : opts?.family === "IPv4" ? 4 : opts?.family === "IPv6" ? 6 : undefined;
        const entries = filterFamily(all, wanted);
        if (!entries.length) {
          // 该族地址不存在：交回系统解析，让它给出标准 ENOTFOUND/EAI_NODATA
          systemLookup(hostname, opts, callback as never);
          return;
        }
        if (opts && opts.all) {
          (callback as LookupAllCallback)(null, entries);
        } else {
          (callback as LookupCallback)(null, entries[0].address, entries[0].family);
        }
      },
      (err) => {
        // 策略拒绝必须原样抛给调用方：回落系统解析等于把刚挡下的地址又放行一次。
        if (err instanceof BlockedTargetError) {
          (callback as unknown as (e: Error) => void)(err);
          return;
        }
        systemLookup(hostname, opts, callback as never);
      },
    );
  };
  (patched as unknown as Record<string, unknown>).__cachedLookup = true;
  dns.lookup = patched as typeof dns.lookup;
}
