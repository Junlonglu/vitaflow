/**
 * BYOK 出站守卫（RF-012）：用户填的地址不能把 AI 服务变成内网代理。
 *
 * 三件事分别验证：
 * 1. 地址分类（哪些算内网/保留/元数据）；
 * 2. 每轮解析：解析到内网就拒绝，公网才登记；
 * 3. 登记之后，**拨号那次解析**同样被拦——这是 DNS rebinding 的唯一有效防线，
 *    因为「先查一次公网再连内网」的窗口只有在这一层才关得上。
 */
import assert from "node:assert/strict";
import dns from "node:dns";
import { createRequire } from "node:module";
import { test } from "node:test";

import { installCachedLookup } from "./dns-lookup.js";
import {
  assertPublicTarget,
  redirectGuardInit,
  guardHost,
  installOutboundFetchGuard,
  isGuardedHost,
  isPrivateAddress,
  resetGuardedHosts,
  resetTargetResolver,
  setTargetResolver,
} from "./ssrf.js";

const dnsCjs = createRequire(import.meta.url)("node:dns") as typeof import("node:dns");
installCachedLookup();
installOutboundFetchGuard();

function lookup(hostname: string): Promise<NodeJS.ErrnoException | null> {
  return new Promise((resolve) => {
    dnsCjs.lookup(hostname, (err: NodeJS.ErrnoException | null) => resolve(err ?? null));
  });
}

test("classifies private, loopback, link-local and metadata addresses", () => {
  for (const address of [
    "127.0.0.1",
    "10.1.2.3",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.1.1",
    "169.254.169.254",
    "100.64.0.1",
    "0.0.0.0",
    "::1",
    "fc00::1",
    "fd12:3456::1",
    "fe80::1",
    "::ffff:127.0.0.1",
  ]) {
    assert.equal(isPrivateAddress(address), true, `${address} should be private`);
  }
  for (const address of ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"]) {
    assert.equal(isPrivateAddress(address), false, `${address} should be public`);
  }
});

test("rejects a base url pointing at a private ip literal", async () => {
  const result = await assertPublicTarget("http://127.0.0.1:8080/v1");
  assert.equal(result.ok, false);
  assert.match(result.error ?? "", /内网/);
});

test("rejects localhost by name", async () => {
  const result = await assertPublicTarget("http://localhost:8080/v1");
  assert.equal(result.ok, false);
  assert.match(result.error ?? "", /localhost/);
});

test("rejects a domain that resolves to a private address", async () => {
  setTargetResolver(async () => ["192.168.0.10"]);
  try {
    const result = await assertPublicTarget("https://rebind.example.com/v1");
    assert.equal(result.ok, false);
    assert.match(result.error ?? "", /192\.168\.0\.10/);
  } finally {
    resetTargetResolver();
  }
});

test("rejects when any resolved address is private", async () => {
  // 轮询 DNS 里混一个内网地址，客户端可能正好选中它。
  setTargetResolver(async () => ["93.184.216.34", "10.0.0.7"]);
  try {
    const result = await assertPublicTarget("https://mixed.example.com/v1");
    assert.equal(result.ok, false);
  } finally {
    resetTargetResolver();
  }
});

test("rejects a non-http scheme", async () => {
  const result = await assertPublicTarget("ftp://example.com/v1");
  assert.equal(result.ok, false);
  assert.match(result.error ?? "", /http/);
});

test("rejects an unresolvable host instead of passing it through", async () => {
  setTargetResolver(async () => []);
  try {
    const result = await assertPublicTarget("https://nowhere.example.com/v1");
    assert.equal(result.ok, false);
    assert.match(result.error ?? "", /无法解析/);
  } finally {
    resetTargetResolver();
  }
});

test("accepts a public target and registers it for the connection-time check", async () => {
  resetGuardedHosts();
  setTargetResolver(async () => ["93.184.216.34"]);
  try {
    const result = await assertPublicTarget("https://api.openai.com/v1");
    assert.equal(result.ok, true);
    assert.equal(isGuardedHost("api.openai.com"), true);
  } finally {
    resetTargetResolver();
    resetGuardedHosts();
  }
});

test("connection-time lookup refuses a guarded host that now resolves private", async () => {
  resetGuardedHosts();
  guardHost("rebound.example.com");
  const mock = test.mock.method(dns.promises, "resolve4", async () => ["127.0.0.1"]);
  test.mock.method(dns.promises, "resolve6", async () => []);
  try {
    const err = await lookup("rebound.example.com");
    assert.ok(err, "lookup must fail rather than hand back a loopback address");
    assert.match(String(err?.message ?? ""), /private address/);
    assert.ok(mock.mock.calls.length >= 1);
  } finally {
    resetGuardedHosts();
    mock.mock.restore();
  }
});

test("unguarded hosts keep resolving normally (self-hosted platform models)", async () => {
  resetGuardedHosts();
  const mock = test.mock.method(dns.promises, "resolve4", async () => ["192.168.1.20"]);
  test.mock.method(dns.promises, "resolve6", async () => []);
  try {
    // 不要用 .local/.internal/.lan：它们本来就绕过内置解析器走系统解析（mDNS/企业 DNS），
    // 那样这个用例测的就不是「登记与否」而是那条豁免规则了。
    const err = await lookup("internal-llm.corp.example");
    assert.equal(err, null, "平台自托管模型跑在内网是正常部署，不该被拦");
    assert.ok(mock.mock.calls.length >= 1);
  } finally {
    mock.mock.restore();
  }
});

test("the redirect guard rewrites only guarded hosts", () => {
  resetGuardedHosts();
  guardHost("guarded.example.com");

  assert.equal(redirectGuardInit("https://guarded.example.com/v1/chat")?.redirect, "error");
  assert.equal(redirectGuardInit(new URL("https://guarded.example.com/v1"))?.redirect, "error");
  // 别的域名照旧：平台 provider 要走正常重定向。
  assert.equal(redirectGuardInit("https://api.openai.com/v1"), undefined);
  // 调用方显式指定过 redirect 就不覆盖。
  assert.equal(redirectGuardInit("https://guarded.example.com/v1", { redirect: "follow" })?.redirect, "follow");
  // 不是合法 URL 时不改变行为，让 fetch 自己报错。
  assert.equal(redirectGuardInit("not a url"), undefined);

  resetGuardedHosts();
});
