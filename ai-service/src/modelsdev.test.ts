import assert from "node:assert/strict";
import test from "node:test";
import type { CatalogFile } from "./catalog.js";
import {
  enrichCatalogFromModelsDev,
  enrichProviderFromModelsDev,
  fetchModelsDev,
  guessModelsDevKey,
  resetModelsDevCache,
  type ModelsDevData,
} from "./modelsdev.js";

const NOW = new Date("2026-11-14T00:00:00Z");

function devData(): ModelsDevData {
  return {
    deepseek: {
      id: "deepseek",
      name: "DeepSeek",
      models: {
        "deepseek-v4-flash": {
          id: "deepseek-v4-flash",
          name: "DeepSeek V4 Flash",
          reasoning: true,
          modalities: { input: ["text"], output: ["text"] },
          limit: { context: 1_048_576, output: 393_216 },
          cost: { input: 0.33, output: 0.99, cache_read: 0.011 },
          last_updated: "2026-09-01",
        },
        "deepseek-v4-vision": {
          id: "deepseek-v4-vision",
          modalities: { input: ["text", "image"], output: ["text"] },
          limit: { context: 131_072, output: 16_384 },
          last_updated: "2026-10-05",
        },
      },
    },
    zhipuai: {
      id: "zhipuai",
      models: {
        "glm-5.1": { limit: { context: 200_000, output: 32_000 }, reasoning: false },
      },
    },
  };
}

function catalog(key = "deepseek", partial?: Partial<CatalogFile["providers"]["deepseek"]>): CatalogFile {
  return {
    providers: {
      [key]: {
        baseUrl: "https://api.deepseek.com",
        models: [
          { id: "deepseek-v4-flash" },
          { id: "deepseek-v4-vision", contextWindow: 64_000, input: ["text", "image"] },
          { id: "unknown-model" },
        ],
        ...partial,
      },
    },
  };
}

test("enrich fills only missing fields and never overwrites manual values", () => {
  const file = catalog();
  const summary = enrichCatalogFromModelsDev(file, devData(), NOW);
  assert.deepEqual(summary.deepseek, { dev_provider: "deepseek", matched: 2, filled: 6 });

  const flash = file.providers.deepseek.models![0];
  assert.equal(flash.contextWindow, 1_048_576);
  assert.equal(flash.maxTokens, 393_216);
  assert.equal(flash.reasoning, true);
  assert.deepEqual(flash.input, ["text"]);
  assert.deepEqual(flash.cost, { input: 0.33, output: 0.99, cacheRead: 0.011, cacheWrite: 0 });
  assert.equal(flash.meta?.models_dev?.provider, "deepseek");
  assert.equal(flash.meta?.models_dev?.updated_at, "2026-09-01");
  assert.equal(flash.meta?.models_dev?.synced_at, NOW.toISOString());

  // Manual contextWindow / input stay untouched; only maxTokens is filled.
  const vision = file.providers.deepseek.models![1];
  assert.equal(vision.contextWindow, 64_000);
  assert.deepEqual(vision.input, ["text", "image"]);
  assert.equal(vision.maxTokens, 16_384);
  assert.equal(vision.cost, undefined);
  assert.equal(vision.meta?.models_dev?.updated_at, "2026-10-05");

  // No models.dev entry: model is left completely alone.
  const unknown = file.providers.deepseek.models![2];
  assert.equal(unknown.contextWindow, undefined);
  assert.equal(unknown.meta, undefined);
});

test("provider key mismatch falls back to exact model-id match across providers", () => {
  const file = catalog("glm", {
    baseUrl: "https://open.bigmodel.cn",
    models: [{ id: "glm-5.1" }],
  });
  const summary = enrichProviderFromModelsDev("glm", file.providers.glm, devData(), NOW);
  assert.equal(summary.dev_provider, null);
  assert.equal(summary.matched, 1);
  assert.equal(file.providers.glm.models![0].contextWindow, 200_000);
  assert.equal(file.providers.glm.models![0].meta?.models_dev?.provider, "zhipuai");
});

test("provider key match is case-insensitive; ambiguous ids are not guessed", () => {
  const file = catalog("DeepSeek");
  const summary = enrichProviderFromModelsDev("DeepSeek", file.providers.DeepSeek, devData(), NOW);
  assert.equal(summary.dev_provider, "deepseek");
  assert.equal(summary.matched, 2);
  assert.equal(file.providers.DeepSeek.models![0].maxTokens, 393_216);
});

test("fetchModelsDev caches responses and surfaces HTTP errors as Chinese messages", async () => {
  resetModelsDevCache();
  const okFetch = (async () =>
    new Response(JSON.stringify(devData()), { status: 200, headers: { "content-type": "application/json" } })) as unknown as typeof fetch;
  const data = await fetchModelsDev(okFetch);
  const again = await fetchModelsDev(okFetch);
  assert.equal(again, data);

  resetModelsDevCache();
  const failing = (async () => new Response("boom", { status: 500 })) as unknown as typeof fetch;
  await assert.rejects(() => fetchModelsDev(failing), /HTTP 500/);

  resetModelsDevCache();
  const networkError = (async () => {
    throw new Error("ECONNREFUSED");
  }) as unknown as typeof fetch;
  await assert.rejects(() => fetchModelsDev(networkError), /无法访问模型资料库/);
  resetModelsDevCache();
});

test("guessModelsDevKey recognises vendor hosts and falls back to the first label", () => {
  assert.equal(guessModelsDevKey("https://api.deepseek.com/v1"), "deepseek");
  assert.equal(guessModelsDevKey("https://open.bigmodel.cn/api/paas/v4"), "zhipuai");
  assert.equal(guessModelsDevKey("https://api.moonshot.cn/v1"), "moonshotai");
  assert.equal(guessModelsDevKey("https://ark.cn-beijing.volces.com/api/v3"), "volcengine");
  assert.equal(guessModelsDevKey("https://dashscope.aliyuncs.com/compatible-mode/v1"), "alibaba");
  // unknown vendor: first label that is not api/www/open
  assert.equal(guessModelsDevKey("https://api.acme-llm.dev/v1"), "acme-llm");
  assert.equal(guessModelsDevKey(""), null);
  assert.equal(guessModelsDevKey("not a url"), null);
});
