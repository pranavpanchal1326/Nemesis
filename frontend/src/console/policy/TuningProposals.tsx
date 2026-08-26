"use client";

import { useState } from "react";

import type { components } from "@/generated/api";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

type Proposals = components["schemas"]["ProposalsResponse"];

/**
 * What the reverted merges suggest — §E19.8, §13.3.
 *
 * This is the half of §E19.8's example sentence that no other screen can
 * produce: *"…6 of which reviewers later reverted."* `simulation/tuning.py` is
 * explicit that a revert is the only human dedup signal the catalog carries,
 * and that everything follows from it:
 *
 * > **Therefore a proposal can only ever be more conservative.** Every revert
 * > says a merge was wrong; nothing says a merge was missed.
 *
 * That asymmetry is rendered as a sentence rather than left as a property of
 * the numbers, because an operator looking at "0.82 → 0.88" cannot tell from
 * the arrow that the system is *incapable* of proposing the other direction —
 * and if they think it could have, they will read a lack of downward proposals
 * as evidence that none is warranted.
 *
 * **It is behind a button, and that is the endpoint's own design.** The route
 * is a `POST` that writes nothing, separated from `/tuning/dedup/draft` so that
 * *"show me what the data suggests"* is not the same request as *"put that in
 * front of an approver"*. A screen that computed proposals on load would
 * collapse that distinction back into one.
 */
export function TuningProposals({ strings }: { readonly strings: Strings }) {
  const [state, setState] = useState<"idle" | "asking">("idle");
  const [result, setResult] = useState<Proposals | null>(null);
  const [failed, setFailed] = useState(false);

  async function ask() {
    setState("asking");
    setFailed(false);
    try {
      const response = await fetch("/api/policy/tuning/dedup", { method: "POST" });
      if (!response.ok) {
        setFailed(true);
      } else {
        setResult((await response.json()) as Proposals);
      }
    } catch {
      setFailed(true);
    }
    setState("idle");
  }

  return (
    <div className="policy__tuning">
      <button
        type="button"
        className="policy__action type-caption"
        disabled={state === "asking"}
        onClick={() => {
          void ask();
        }}
      >
        {t(strings, "tuning.ask")}
      </button>

      <p className="policy__why type-caption">{t(strings, "tuning.direction")}</p>

      {failed ? <p className="type-caption">{t(strings, "state.error")}</p> : null}

      {result === null ? null : result.proposals.length === 0 ? (
        <p className="type-caption">{t(strings, "tuning.none")}</p>
      ) : (
        <ul className="policy__proposals">
          {result.proposals.map((proposal) => (
            <li key={proposal.category ?? "*"} className="type-caption">
              {t(strings, "tuning.row", {
                // `null` is the all-categories band, and it renders as a named
                // scope rather than as an empty cell — a blank there reads as a
                // missing category rather than as "every category".
                category: proposal.category ?? t(strings, "tuning.allCategories"),
                current: proposal.current_threshold.toFixed(2),
                proposed: proposal.proposed_threshold.toFixed(2),
                reverts: proposal.revert_count,
                highest: proposal.highest_reverted_confidence.toFixed(2),
              })}
            </li>
          ))}
        </ul>
      )}

      {result?.direction === undefined ? null : (
        <p className="policy__why type-caption">{notTranslatable(result.direction)}</p>
      )}
    </div>
  );
}
