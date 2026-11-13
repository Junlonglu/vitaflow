/**
 * Routing groups (magpie's routing groups, order/rotate/usage subset).
 *
 * A group bundles several catalog models behind one ref ("group/<id>") so a
 * model strategy can survive a dead provider: selection-time failover skips
 * members that failed recently (cooldown) or are missing from the runtime,
 * and a conversation sticks to the member that answered it — switching
 * upstream mid-conversation wastes the vendor's prompt cache, which is the
 * main cost benefit of long-context chat.
 *
 * Deliberately NOT done here: mid-turn failover. Once a turn has streamed
 * anything to the user, switching members would duplicate visible events, so
 * a failed turn only cools the member down for future turns.
 */
import type { CatalogFile, CatalogRoutingGroup } from "./catalog.js";
import type { RealRuntime } from "./real.js";

export type RoutingStrategy = "order" | "rotate" | "usage";

export interface ResolvedMember {
  ref?: string;
  error?: string;
}

const FAILURE_COOLDOWN_MS = 5 * 60_000;
const STICKY_TTL_MS = 30 * 60_000;

const memberFailureUntil = new Map<string, number>();
const stickyBySession = new Map<number, { ref: string; at: number }>();
const rotateCursor = new Map<string, number>();
const memberUsageCount = new Map<string, number>();

export function resetRoutingState(): void {
  memberFailureUntil.clear();
  stickyBySession.clear();
  rotateCursor.clear();
  memberUsageCount.clear();
}

function memberExists(runtime: RealRuntime, ref: string): boolean {
  const slash = ref.indexOf("/");
  if (slash <= 0) return false;
  return Boolean(runtime.modelRuntime.getModel(ref.slice(0, slash), ref.slice(slash + 1)));
}

function isCooling(ref: string, now: number): boolean {
  const until = memberFailureUntil.get(ref);
  return until !== undefined && until > now;
}

function pickByStrategy(groupId: string, strategy: RoutingStrategy, candidates: string[]): string {
  if (!candidates.length) return "";
  if (strategy === "rotate") {
    const cursor = rotateCursor.get(groupId) ?? 0;
    rotateCursor.set(groupId, cursor + 1);
    return candidates[cursor % candidates.length];
  }
  if (strategy === "usage") {
    return [...candidates].sort((a, b) => (memberUsageCount.get(a) ?? 0) - (memberUsageCount.get(b) ?? 0))[0];
  }
  return candidates[0];
}

export function findRoutingGroup(catalog: CatalogFile, groupId: string): CatalogRoutingGroup | undefined {
  return (catalog.routing_groups || []).find((group) => group.id === groupId);
}

/**
 * Resolve a "group/<id>" ref into a concrete member ref. Existing members are
 * preferred; cooling members are skipped unless everything is cooling.
 */
export function resolveRoutingGroup(
  catalog: CatalogFile,
  runtime: RealRuntime,
  ref: string,
  sessionId?: number,
  now: number = Date.now(),
): ResolvedMember {
  const groupId = ref.startsWith("group/") ? ref.slice("group/".length) : "";
  const group = groupId ? findRoutingGroup(catalog, groupId) : undefined;
  if (!group) return { error: `路由组 ${ref} 不在 models.json 中` };

  const existing = group.members.filter((member) => memberExists(runtime, member));
  if (!existing.length) {
    return { error: `路由组 ${group.id} 的成员都没有可用模型，请检查模型目录` };
  }
  // Session stickiness wins as long as the member is still alive (cooldown
  // members are dropped so a cooling sticky member doesn't burn every turn).
  if (sessionId !== undefined && sessionId > 0) {
    const sticky = stickyBySession.get(sessionId);
    if (sticky && now - sticky.at < STICKY_TTL_MS && existing.includes(sticky.ref) && !isCooling(sticky.ref, now)) {
      memberUsageCount.set(sticky.ref, (memberUsageCount.get(sticky.ref) ?? 0) + 1);
      return { ref: sticky.ref };
    }
  }
  const candidates = existing.filter((member) => !isCooling(member, now));
  const picked = pickByStrategy(group.id, group.strategy, candidates.length ? candidates : existing);
  if (!picked) return { error: `路由组 ${group.id} 没有可用成员` };
  memberUsageCount.set(picked, (memberUsageCount.get(picked) ?? 0) + 1);
  if (sessionId !== undefined && sessionId > 0) {
    stickyBySession.set(sessionId, { ref: picked, at: now });
  }
  return { ref: picked };
}

/** A failed turn cools its member down; future turns skip it first. */
export function noteRoutingFailure(ref: string, now: number = Date.now()): void {
  if (ref.startsWith("group/")) return;
  memberFailureUntil.set(ref, now + FAILURE_COOLDOWN_MS);
}

/** Success clears any cooldown and refreshes the session's sticky member. */
export function noteRoutingSuccess(sessionId: number | undefined, ref: string, now: number = Date.now()): void {
  if (ref.startsWith("group/")) return;
  memberFailureUntil.delete(ref);
  if (sessionId !== undefined && sessionId > 0) {
    stickyBySession.set(sessionId, { ref, at: now });
  }
}
