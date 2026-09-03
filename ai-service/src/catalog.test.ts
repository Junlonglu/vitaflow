import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  CatalogError,
  listApiKeyEnvNames,
  maskApiKey,
  mutateCatalog,
  readCatalog,
  renderProviders,
  validateModelBody,
  validateProviderBody,
  validateProviderKey,
} from "./catalog.js";

test("listApiKeyEnvNames returns sorted *_API_KEY names only, never values", () => {
  const names = listApiKeyEnvNames({
    DEEPSEEK_API_KEY: "sk-secret",
    SILICONFLOW_API_KEY: "sk-other",
    lowercase_api_key: "sk-no",
    AI_SERVICE_TOKEN: "token",
    PATH: "whatever",
  });
  assert.deepEqual(names, ["DEEPSEEK_API_KEY", "SILICONFLOW_API_KEY"]);
  assert.equal(JSON.stringify(names).includes("sk-secret"), false);
});

async function tempAgentDir(): Promise<string> {
  return mkdtemp(join(tmpdir(), "vitaflow-catalog-"));
}

test("readCatalog tolerates a missing file", async () => {
  const dir = await tempAgentDir();
  try {
    const catalog = await readCatalog(dir);
    assert.deepEqual(catalog, { providers: {} });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("readCatalog rejects invalid JSON", async () => {
  const dir = await tempAgentDir();
  try {
    await writeFile(join(dir, "models.json"), "{ not json", "utf8");
    await assert.rejects(() => readCatalog(dir), CatalogError);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("mutateCatalog round-trips providers and models atomically", async () => {
  const dir = await tempAgentDir();
  try {
    await mutateCatalog(dir, (catalog) => {
      catalog.providers["deepseek"] = {
        name: "DeepSeek",
        baseUrl: "https://api.deepseek.com",
        apiKey: "sk-test",
        api: "openai-completions",
        models: [{ id: "deepseek-chat", name: "DeepSeek Chat", contextWindow: 128000 }],
      };
    });
    const raw = JSON.parse(await readFile(join(dir, "models.json"), "utf8"));
    assert.equal(raw.providers.deepseek.models[0].id, "deepseek-chat");

    const views = renderProviders(await readCatalog(dir));
    assert.equal(views.length, 1);
    assert.equal(views[0].api_key_masked, "***");
    assert.equal(views[0].models[0].ref, "deepseek/deepseek-chat");
    assert.equal(views[0].models[0].context_window, 128000);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("mutateCatalog serializes concurrent writes", async () => {
  const dir = await tempAgentDir();
  try {
    await Promise.all(
      ["a", "b", "c"].map((key) =>
        mutateCatalog(dir, (catalog) => {
          catalog.providers[key] = { baseUrl: "https://example.com", models: [] };
        }),
      ),
    );
    const catalog = await readCatalog(dir);
    assert.deepEqual(Object.keys(catalog.providers).sort(), ["a", "b", "c"]);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("validators enforce keys, base urls and model fields", () => {
  assert.equal(validateProviderKey("deepseek"), "deepseek");
  assert.throws(() => validateProviderKey("-bad"), CatalogError);
  assert.throws(() => validateProviderKey(""), CatalogError);

  const body = validateProviderBody({ name: "DeepSeek", baseUrl: "https://api.deepseek.com", apiKey: " " });
  assert.equal(body.apiKey, "");
  assert.throws(() => validateProviderBody({ name: "x", baseUrl: "api.deepseek.com" }), CatalogError);

  const model = validateModelBody({ name: "Chat", input: ["text", "image"], contextWindow: 128000, maxTokens: 8192 });
  assert.deepEqual(model.input, ["text", "image"]);
  assert.throws(() => validateModelBody({ input: ["video"] }), CatalogError);
  assert.throws(() => validateModelBody({ contextWindow: -1 }), CatalogError);
});

test("maskApiKey never leaks the raw key", () => {
  assert.equal(maskApiKey(undefined), null);
  assert.equal(maskApiKey("  "), null);
  assert.equal(maskApiKey("short"), "***");
  const masked = maskApiKey("sk-1234567890abcd");
  assert.ok(masked!.startsWith("sk-"));
  assert.ok(masked!.endsWith("abcd"));
  assert.ok(!masked!.includes("1234567890"));
});
