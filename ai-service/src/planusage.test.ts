import assert from "node:assert/strict";
import test from "node:test";
import type { CatalogProvider } from "./catalog.js";
import {
  aliACSSign,
  aliPlanSite,
  aliReadWindows,
  detectPlan,
  queryPlanUsage,
  resetPlanUsageCache,
  tc3Sign,
  tencentPlanRegion,
  volcPlanOf,
  volcSign,
  type PlanUsageResult,
} from "./planusage.js";

// The pin helpers mirror probe.ts's resolveApiKey expansion for $ENV refs.
function provider(partial: Partial<CatalogProvider> & { baseUrl: string }): CatalogProvider {
  return { api: "openai-completions", models: [], ...partial };
}

test("detectPlan infers vendor plans from endpoint URLs only", () => {
  assert.deepEqual(detectPlan(provider({ baseUrl: "https://ark.cn-beijing.volces.com/api/coding/v3" })), {
    vendor: "volcengine",
    plan: "Coding Plan",
    region: "cn-beijing",
  });
  assert.deepEqual(detectPlan(provider({ baseUrl: "https://ark.cn-shanghai.volces.com/api/plan" })), {
    vendor: "volcengine",
    plan: "Agent Plan",
    region: "cn-shanghai",
  });
  // Pay-as-you-go (/api/v3) has no plan window.
  assert.equal(detectPlan(provider({ baseUrl: "https://ark.cn-beijing.volces.com/api/v3" })), null);
  assert.equal(detectPlan(provider({ baseUrl: "https://api.deepseek.com" })), null);

  assert.deepEqual(aliPlanSite(provider({ baseUrl: "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1" })), {
    region: "cn-beijing",
    mint: "modelstudio.cn-beijing.aliyuncs.com",
    gateway: "bailian-cs.console.aliyun.com",
    action: "BroadScopeAspnGateway",
  });
  assert.equal(aliPlanSite(provider({ baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1" })), null);

  assert.equal(tencentPlanRegion(provider({ baseUrl: "https://tokenhub.tencentmaas.com/plan/v3" })), "ap-guangzhou");
  assert.equal(tencentPlanRegion(provider({ baseUrl: "https://tokenhub-intl.tencentmaas.com/plan/anthropic" })), "ap-singapore");
  assert.equal(tencentPlanRegion(provider({ baseUrl: "https://tokenhub.tencentmaas.com/v1" })), null);
});

test("volcPlanOf requires the coding/plan paths, not just the ark host", () => {
  assert.equal(volcPlanOf(provider({ baseUrl: "https://ark.cn-beijing.volces.com/api/v3" }))?.action, undefined);
  assert.equal(volcPlanOf(provider({ baseUrl: "https://ark.cn-beijing.volces.com/api/coding/v3" }))?.action, "GetCodingPlanUsage");
});

test("volcSign matches the signature pinned by volc-sdk-golang (magpie TestVolcSignMatchesSDK)", () => {
  const now = new Date("2026-10-09T08:30:15Z");
  const { authorization, xDate, contentSha } = volcSign(
    "AKLTexample",
    "c2VjcmV0LWV4YW1wbGU=",
    "cn-beijing",
    { Action: "GetCodingPlanUsage", Version: "2024-01-01" },
    "{}",
    now,
  );
  assert.equal(
    authorization,
    "HMAC-SHA256 Credential=AKLTexample/20261009/cn-beijing/ark/request, SignedHeaders=content-type;host;x-content-sha256;x-date, Signature=deb80c0cd8bd77768ca40f6d47c7204621c0994428f6ab77e23b76e15dc4c047",
  );
  assert.equal(xDate, "20261009T083015Z");
  assert.equal(contentSha, "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a");

  const shanghai = volcSign("AKLTexample", "c2VjcmV0LWV4YW1wbGU=", "cn-shanghai", { Action: "GetAFPUsage", Version: "2024-01-01" }, "{}", now);
  assert.match(shanghai.authorization, /Signature=8a241be0996c93de4dfcd9e8b1db37494bf63170804fcaebe8380ff7dca2bb23$/);
});

test("tc3Sign matches Tencent Cloud's signing-doc example (magpie TestTC3SignMatchesDoc)", () => {
  const esc = "#u672a#u547d#u540d".replaceAll("#", "\\");
  const payload = `{"Limit": 1, "Filters": [{"Values": ["${esc}"], "Name": "instance-name"}]}`;
  const id = `AKID${"*".repeat(32)}`;
  const key = "*".repeat(32);
  const got = tc3Sign(id, key, "cvm", "cvm.tencentcloudapi.com", "DescribeInstances", payload, new Date(1551113065_000));
  assert.equal(
    got,
    "TC3-HMAC-SHA256 Credential=AKID********************************/2019-02-25/cvm/tc3_request, SignedHeaders=content-type;host;x-tc-action, Signature=10b1a37a7301a02ca19a647ad722d5e43b4b3cff309d421d85b46093f6ab6c4f".replace("********************************", "*".repeat(32)),
  );
});

test("aliACSSign produces the ACS3 envelope shape with the empty-body hash", () => {
  const now = new Date("2026-11-12T01:02:03Z");
  const headers = aliACSSign("AKID", "SECRET", "modelstudio.cn-beijing.aliyuncs.com", "/modelstudio/cli/generateAccessToken", "GenerateCLIAccessToken", "2026-02-10", now, "nonce1");
  const empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
  assert.equal(headers["x-acs-content-sha256"], empty);
  assert.equal(headers["x-acs-date"], "2026-11-12T01:02:03Z");
  const signed = "content-type;host;x-acs-action;x-acs-content-sha256;x-acs-date;x-acs-signature-nonce;x-acs-version";
  assert.match(headers.authorization, new RegExp(`^ACS3-HMAC-SHA256 Credential=AKID,SignedHeaders=${signed.replaceAll(";", ";")},Signature=[0-9a-f]{64}$`));
  // Another secret must sign differently.
  const other = aliACSSign("AKID", "OTHER", "modelstudio.cn-beijing.aliyuncs.com", "/modelstudio/cli/generateAccessToken", "GenerateCLIAccessToken", "2026-02-10", now, "nonce1");
  assert.notEqual(other.authorization, headers.authorization);
});

test("aliReadWindows pairs per<N><Unit> fields into windows, shortest first", () => {
  const windows = aliReadWindows({
    per1MonthPercentage: "0.25",
    per1MonthResetTime: 1780000000000,
    per5HourPercentage: 0.1,
    per5HourResetTime: 1770000000000,
    per1WeekPercentage: 0.5,
    planName: "x",
    per1DayPercentage: null,
  });
  assert.deepEqual(windows.map((w) => w.name), ["5 hours", "Weekly", "Month"]);
  assert.equal(windows[0].used_percent, 10);
  assert.equal(windows[2].used_percent, 25);
  assert.equal(windows[0].span_hours, 5);
  assert.equal(windows[2].resets_at, new Date(1780000000000).toISOString());
  assert.deepEqual(aliReadWindows({ x: 1 }), []);
});
