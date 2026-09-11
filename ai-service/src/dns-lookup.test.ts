/**
 * dns.lookup 补丁的契约：快速路径走 resolve4/resolve6 + 缓存，IP 字面量与
 * 旧式签名（(host, cb) / (host, family, cb) / family 字符串）保持兼容。
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import dns from "node:dns";
import { createRequire } from "node:module";
import { installCachedLookup } from "./dns-lookup.js";

const dnsCjs = createRequire(import.meta.url)("node:dns") as typeof import("node:dns");

installCachedLookup();

function lookup(hostname: string, options?: number | import("node:dns").LookupOptions): Promise<{ address?: string; family?: number; all?: import("node:dns").LookupAddress[]; err?: NodeJS.ErrnoException }> {
  return new Promise((resolve) => {
    const cb = (err: NodeJS.ErrnoException | null, address: string, family: number) => resolve({ err: err ?? undefined, address, family });
    const cbAll = (err: NodeJS.ErrnoException | null, addresses: import("node:dns").LookupAddress[]) => resolve({ err: err ?? undefined, all: addresses });
    if (options === undefined) dnsCjs.lookup(hostname, cb);
    else if (typeof options === "number") dnsCjs.lookup(hostname, options, cb);
    else dnsCjs.lookup(hostname, options, options.all ? (cbAll as never) : (cb as never));
  });
}

test("IP literals bypass the resolver entirely", async () => {
  const mock = test.mock.method(dns.promises, "resolve4", async () => {
    throw new Error("must not be called for IP literals");
  });
  const v4 = await lookup("127.0.0.1");
  assert.equal(v4.address, "127.0.0.1");
  assert.equal(v4.family, 4);
  const v6 = await lookup("::1");
  assert.equal(v6.address, "::1");
  assert.equal(v6.family, 6);
  assert.equal(mock.mock.calls.length, 0);
});

test("resolves via resolve4, caches, and dedupes family filtering", async () => {
  const resolve4 = test.mock.method(dns.promises, "resolve4", async (hostname: string) =>
    hostname === "cache-test.example.com" ? ["93.184.216.34", "93.184.216.35"] : [],
  );
  test.mock.method(dns.promises, "resolve6", async () => []);

  const first = await lookup("cache-test.example.com");
  assert.equal(first.address, "93.184.216.34");
  assert.equal(first.family, 4);

  // 第二次命中缓存：resolve4 只被调用一次
  const second = await lookup("cache-test.example.com");
  assert.equal(second.address, "93.184.216.34");
  assert.equal(resolve4.mock.calls.length, 1);

  const all = await lookup("cache-test.example.com", { all: true });
  assert.deepEqual(all.all?.map((entry) => ({ address: entry.address, family: entry.family })), [
    { address: "93.184.216.34", family: 4 },
    { address: "93.184.216.35", family: 4 },
  ]);
});

test("legacy (host, cb) and (host, family, cb) signatures still work", async () => {
  test.mock.method(dns.promises, "resolve4", async (hostname: string) =>
    hostname === "legacy-test.example.com" ? ["1.2.3.4"] : [],
  );
  test.mock.method(dns.promises, "resolve6", async () => []);

  const twoArg = await lookup("legacy-test.example.com");
  assert.equal(twoArg.address, "1.2.3.4");

  const numericFamily = await lookup("legacy-test.example.com", 4);
  assert.equal(numericFamily.address, "1.2.3.4");
  assert.equal(numericFamily.family, 4);
});

test("string family IPv6 with no v6 records falls back to system lookup", async () => {
  test.mock.method(dns.promises, "resolve4", async () => ["1.2.3.4"]);
  test.mock.method(dns.promises, "resolve6", async () => []);

  // .invalid 域名必然 NXDOMAIN：兜底走系统解析，返回标准错误
  const result = await lookup("no-v6-record.invalid", { family: "IPv6" } as import("node:dns").LookupOptions);
  assert.ok(result.err, "expected ENOTFOUND from system fallback");
  assert.equal(result.err?.code, "ENOTFOUND");
});

test("resolve4 failure falls back to system lookup error", async () => {
  test.mock.method(dns.promises, "resolve4", async () => {
    throw Object.assign(new Error("fail"), { code: "EAI_AGAIN" });
  });
  test.mock.method(dns.promises, "resolve6", async () => []);

  const result = await lookup("resolve4-fails.invalid");
  assert.ok(result.err, "expected error from system fallback");
  assert.equal(result.err?.code, "ENOTFOUND");
});
