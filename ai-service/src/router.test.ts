/**
 * Routing group tests (selection-time failover + session stickiness).
 *
 * ModelRuntime is only used through getModel(provider, id) here, so the runtime
 * is stubbed with a lookup table instead of spinning up the SDK.
 */
import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import type { CatalogFile } from "./catalog.js";
import {
  findRoutingGroup,
  noteRoutingFailure,
  noteRoutingSuccess,
  resolveRoutingGroup,
  resetRoutingState,
} from "./router.js";
import type { RealRuntime } from "./real.js";

function fakeRuntime(present: string[]): RealRuntime {
  const known = new Set(present);
  return {
    kind: "real",
    modelRuntime: {
      getModel: (provider: string, id: string) => (known.has(`${provider}/${id}`) ? ({} as never) : undefined),
    } as unknown as RealRuntime["modelRuntime"],
  } as RealRuntime;
}

function catalogWith(members: string[], strategy: "order" | "rotate" | "usage" = "order"): CatalogFile {
  return { providers: {}, routing_groups: [{ id: "main", strategy, members }] };
}

describe("resolveRoutingGroup", () => {
  beforeEach(() => resetRoutingState());

  it("fails with a clear error for an unknown group", () => {
    const result = resolveRoutingGroup(catalogWith(["a/m1"]), fakeRuntime(["a/m1"]), "group/nope");
    assert.equal(result.error, "路由组 group/nope 不在 models.json 中");
  });

  it("fails when no member exists in the runtime", () => {
    const result = resolveRoutingGroup(catalogWith(["a/m1", "b/m2"]), fakeRuntime([]), "group/main");
    assert.match(result.error ?? "", /成员都没有可用模型/);
  });

  it("order strategy picks the first existing member", () => {
    const result = resolveRoutingGroup(catalogWith(["a/m1", "b/m2"]), fakeRuntime(["a/m1", "b/m2"]), "group/main");
    assert.equal(result.ref, "a/m1");
  });

  it("skips cooling members unless everything is cooling", () => {
    const runtime = fakeRuntime(["a/m1", "b/m2"]);
    noteRoutingFailure("a/m1");
    assert.equal(resolveRoutingGroup(catalogWith(["a/m1", "b/m2"]), runtime, "group/main").ref, "b/m2");
    // All cooling → fall back to the full list rather than refusing the turn;
    // order still picks its first candidate.
    noteRoutingFailure("b/m2");
    assert.equal(resolveRoutingGroup(catalogWith(["a/m1", "b/m2"]), runtime, "group/main").ref, "a/m1");
  });

  it("a session sticks to its member across turns", () => {
    const runtime = fakeRuntime(["a/m1", "b/m2"]);
    const catalog = catalogWith(["a/m1", "b/m2"], "rotate");
    const first = resolveRoutingGroup(catalog, runtime, "group/main", 7);
    const second = resolveRoutingGroup(catalog, runtime, "group/main", 7);
    assert.equal(first.ref, second.ref);
  });

  it("stickiness expires after the TTL", () => {
    const runtime = fakeRuntime(["a/m1", "b/m2"]);
    const catalog = catalogWith(["a/m1", "b/m2"], "rotate");
    const t0 = 1_000_000;
    const first = resolveRoutingGroup(catalog, runtime, "group/main", 7, t0);
    // rotate would give the other member on a fresh pick; the sticky window is 30min.
    const sticky = resolveRoutingGroup(catalog, runtime, "group/main", 7, t0 + 10 * 60_000);
    assert.equal(sticky.ref, first.ref);
    const rotated = resolveRoutingGroup(catalog, runtime, "group/main", 7, t0 + 31 * 60_000);
    assert.notEqual(rotated.ref, first.ref);
  });

  it("a cooling sticky member is dropped instead of burning the turn", () => {
    const runtime = fakeRuntime(["a/m1", "b/m2"]);
    const catalog = catalogWith(["a/m1", "b/m2"], "order");
    const t0 = 1_000_000;
    const first = resolveRoutingGroup(catalog, runtime, "group/main", 7, t0);
    assert.equal(first.ref, "a/m1");
    noteRoutingFailure(first.ref!, t0 + 60_000);
    const next = resolveRoutingGroup(catalog, runtime, "group/main", 7, t0 + 120_000);
    assert.equal(next.ref, "b/m2");
  });

  it("rotate strategy alternates members for different sessions", () => {
    const runtime = fakeRuntime(["a/m1", "b/m2"]);
    const catalog = catalogWith(["a/m1", "b/m2"], "rotate");
    const one = resolveRoutingGroup(catalog, runtime, "group/main", 1);
    const two = resolveRoutingGroup(catalog, runtime, "group/main", 2);
    assert.notEqual(one.ref, two.ref);
  });

  it("usage strategy prefers the least used member", () => {
    const runtime = fakeRuntime(["a/m1", "b/m2"]);
    const catalog = catalogWith(["a/m1", "b/m2"], "usage");
    // First pick is a tie → first member; a fresh session now prefers the other.
    assert.equal(resolveRoutingGroup(catalog, runtime, "group/main", 1).ref, "a/m1");
    assert.equal(resolveRoutingGroup(catalog, runtime, "group/main", 2).ref, "b/m2");
  });
});

describe("routing notes", () => {
  beforeEach(() => resetRoutingState());

  it("noteRoutingSuccess clears a failure cooldown", () => {
    const runtime = fakeRuntime(["a/m1", "b/m2"]);
    const catalog = catalogWith(["a/m1", "b/m2"]);
    noteRoutingFailure("a/m1");
    assert.equal(resolveRoutingGroup(catalog, runtime, "group/main", undefined, 1_000_000).ref, "b/m2");
    noteRoutingSuccess(undefined, "a/m1");
    assert.equal(resolveRoutingGroup(catalog, runtime, "group/main", undefined, 1_000_000).ref, "a/m1");
  });

  it("group refs never enter the failure/sticky maps", () => {
    noteRoutingFailure("group/main");
    noteRoutingSuccess(undefined, "group/main");
    // No assertion needed — these must simply not record member state; the
    // next resolution still behaves like a clean slate.
    const runtime = fakeRuntime(["a/m1", "b/m2"]);
    assert.equal(resolveRoutingGroup(catalogWith(["a/m1", "b/m2"]), runtime, "group/main").ref, "a/m1");
  });
});

describe("findRoutingGroup", () => {
  it("finds by id and tolerates a missing groups array", () => {
    const catalog = catalogWith(["a/m1"]);
    assert.equal(findRoutingGroup(catalog, "main")?.strategy, "order");
    assert.equal(findRoutingGroup({ providers: {} }, "main"), undefined);
  });
});
