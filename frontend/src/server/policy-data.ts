import "server-only";

import type { components } from "@/generated/api";
import { controlPlaneHeaders, upstream } from "@/server/upstream";

/**
 * The policy studio's reads — §E19.8, ADR-0028, ADR-0029, ADR-0030.
 *
 * Every one of these is a control-plane read and therefore carries the token
 * (`controlPlaneHeaders()`), which is why they are here and not in a client
 * module: the token is server-held, and `import "server-only"` makes that a
 * build error rather than a review catch.
 *
 * **Reads are `Promise.all`ed by the caller, not chained here.** The studio
 * needs five independent facts about one policy kind — its revisions, what is
 * active, its backtest history, whether an evaluation set gates it, and what
 * the reverts suggest. Fetching them in sequence would make the screen as slow
 * as the sum rather than as the slowest, on a page an operator opens to make a
 * decision.
 */

export type PolicyKind = components["schemas"]["PolicyKind"];
export type PolicyVersionSummary = components["schemas"]["PolicyVersionSummary"];
export type PolicyVersionDetail = components["schemas"]["PolicyVersionDetail"];
export type ActivePolicy = components["schemas"]["ActivePolicyResponse"];
export type RunSummary = components["schemas"]["RunSummary"];
export type EvaluationSet = components["schemas"]["EvaluationSetResponse"];

/** Never cached. Every read here backs a decision an operator is about to take
 *  about what the pipeline does next; a stale revision list is how somebody
 *  activates the revision before the one they meant. */
const live = { headers: controlPlaneHeaders(), cache: "no-store" } as const;

/**
 * **The dedup tuning proposals are deliberately not read here.**
 *
 * `/simulations/tuning/dedup` is a `POST`, and its own docstring says why:
 * *"separating them is what stops 'show me what the data suggests' from being
 * the same request as 'put that in front of an approver'."* It computes and
 * writes nothing, but it is still a request an operator makes rather than a
 * side effect of opening a screen — and a page render that fires it would turn
 * a deliberate act into page furniture. `<TuningProposals>` asks for it when
 * somebody asks for it.
 */
export interface PolicyStudioData {
  readonly kind: PolicyKind;
  readonly versions: readonly PolicyVersionSummary[];
  readonly active: ActivePolicy | null;
  /** The selected revision with its body, for the document and the diff. */
  readonly selected: PolicyVersionDetail | null;
  /** The revision the selected one was based on, so the diff has a left side. */
  readonly previous: PolicyVersionDetail | null;
  readonly runs: readonly RunSummary[];
  /**
   * The evaluation set that gates activation for this kind, if the tenant
   * published one.
   *
   * **Publication is the switch** — `policy/service.py` is explicit that there
   * is no `require_certification` flag: *"A kind with a published set is gated;
   * a kind without one is not."* So the studio decides whether to render the
   * activate control as gated by asking the same question the server asks,
   * rather than by keeping its own idea of which kinds are protected.
   */
  readonly gate: EvaluationSet | null;
}

export async function fetchPolicyStudio(
  kind: PolicyKind,
  revision?: number,
): Promise<PolicyStudioData> {
  const [versions, active, runs, sets] = await Promise.all([
    listVersions(kind),
    getActive(kind),
    listRuns(kind),
    listEvaluationSets(kind),
  ]);

  // The newest revision unless the caller named one. Newest, not active: an
  // operator opens this screen to look at what they are about to activate far
  // more often than to look at what is already running.
  const chosen = revision ?? versions[0]?.revision;
  const selected = chosen === undefined ? null : await getVersion(kind, chosen);
  const previousRevision = selected?.based_on_revision ?? null;
  const previous = previousRevision === null ? null : await getVersion(kind, previousRevision);

  return {
    kind,
    versions,
    active,
    selected,
    previous,
    runs,
    gate: sets.find((set) => set.status === "published") ?? null,
  };
}

async function listVersions(kind: PolicyKind): Promise<readonly PolicyVersionSummary[]> {
  const { data } = await upstream.GET("/api/v1/control-plane/policies", {
    params: { query: { kind, limit: 50 } },
    ...live,
  });
  // Newest first. The endpoint's own order is not promised by the contract, and
  // a studio whose history reversed on a backend change would be a studio whose
  // "current draft" moved.
  return [...(data ?? [])].sort((a, b) => b.revision - a.revision);
}

async function getActive(kind: PolicyKind): Promise<ActivePolicy | null> {
  const { data } = await upstream.GET("/api/v1/control-plane/policies/{kind}/active", {
    params: { path: { kind } },
    ...live,
  });
  return data ?? null;
}

async function getVersion(kind: PolicyKind, revision: number): Promise<PolicyVersionDetail | null> {
  const { data } = await upstream.GET("/api/v1/control-plane/policies/{kind}/{revision}", {
    params: { path: { kind, revision } },
    ...live,
  });
  return data ?? null;
}

async function listRuns(kind: PolicyKind): Promise<readonly RunSummary[]> {
  const { data } = await upstream.GET("/api/v1/control-plane/simulations/runs", {
    params: { query: { kind, limit: 20 } },
    ...live,
  });
  return data ?? [];
}

async function listEvaluationSets(kind: PolicyKind): Promise<readonly EvaluationSet[]> {
  const { data } = await upstream.GET("/api/v1/control-plane/simulations/evaluation-sets", {
    params: { query: { kind } },
    ...live,
  });
  return data ?? [];
}
