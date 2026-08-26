"use client";

import { useId, useState } from "react";

import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

/**
 * The activate control, disabled with its reason attached — §E19.8, §E19.4.
 *
 * > **The activate control is disabled without a backtest.**
 *
 * And the sentence §E19.4 puts under every control like this one:
 *
 * > a client-side check is a convenience, and if it is ever mistaken for the
 * > control, someone will eventually ship a path around it.
 *
 * So this component is careful about what it claims. It knows one fact — does
 * the tenant publish an evaluation set for this kind — because that is the
 * exact fact `_require_certification` switches on. It does **not** try to
 * decide whether a passing certificate exists for this content hash: that
 * lookup is by hash against a labels hash that moves when the set is
 * republished, and a screen that reimplemented it would eventually disagree
 * with the server in the direction that matters — enabling a control the server
 * will refuse.
 *
 * What it does instead is the thing §E19.4 actually asks for: **render the rule
 * legibly before it is hit**, and render the server's refusal verbatim when it
 * comes. The refusal message from `PolicyCertificationError` is a good one — it
 * names the evaluation set, the revision, and what to do next — so it is shown
 * rather than replaced with a generic sentence.
 *
 * A reason is required, and it is required by the contract (`ActivateRequest.
 * reason` is not optional). It is refused here too, before the round trip.
 */
export function ActivateControl({
  kind,
  revision,
  gateCode,
  strings,
}: {
  readonly kind: string;
  readonly revision: number;
  /** The published evaluation set gating this kind, or `null` when none is. */
  readonly gateCode: string | null;
  readonly strings: Strings;
}) {
  const fieldId = useId();
  const noteId = `${fieldId}-note`;
  const [reason, setReason] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "done">("idle");
  /** The server's own words, when it refuses. Not paraphrased. */
  const [refusal, setRefusal] = useState<string | null>(null);

  const gated = gateCode !== null;

  async function activate() {
    setState("sending");
    setRefusal(null);
    try {
      const response = await fetch(`/api/policy/${kind}/${String(revision)}/activate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      if (!response.ok) {
        const body: unknown = await response.json().catch(() => null);
        setRefusal(readTitle(body) ?? t(strings, "activate.failed"));
        setState("idle");
        return;
      }
      setState("done");
    } catch {
      setRefusal(t(strings, "activate.failed"));
      setState("idle");
    }
  }

  return (
    <div className="policy__activate">
      <p className="type-caption" id={noteId}>
        {gated
          ? t(strings, "activate.blocked", { code: gateCode })
          : t(strings, "activate.allowed")}
      </p>
      {!gated ? null : (
        <p className="policy__why type-caption">{t(strings, "activate.blockedWhy")}</p>
      )}

      <label className="type-micro" htmlFor={fieldId}>
        {t(strings, "activate.reason")}
      </label>
      <input
        id={fieldId}
        className="policy__reason type-body"
        type="text"
        value={reason}
        onChange={(event) => {
          setReason(event.target.value);
        }}
      />

      <button
        type="button"
        className="policy__action type-caption"
        // Disabled without a reason, never disabled *because* of the gate: the
        // gate is the server's to enforce, and a control this screen switched
        // off would be the client-side check §E19.4 warns about. What the gate
        // changes here is the sentence above the control, which is the part
        // that teaches the rule.
        disabled={reason.trim() === "" || state !== "idle"}
        aria-describedby={noteId}
        onClick={() => {
          void activate();
        }}
      >
        {t(strings, "activate.action")}
      </button>

      <p className="type-caption" role="status">
        {state === "done" ? (
          <>
            {t(strings, "activate.done")} {t(strings, "activate.reload")}
          </>
        ) : refusal === null ? null : (
          notTranslatable(refusal)
        )}
      </p>
    </div>
  );
}

/**
 * The `title` out of an RFC 9457 problem document.
 *
 * Rendered verbatim through `notTranslatable`, and that is a deliberate
 * exception to §E10.1 rather than an oversight. The certification refusal is
 * the server explaining a guardrail in terms of *this tenant's* evaluation set
 * and *this* revision — data the bundle cannot hold. A generic translated
 * sentence would be less useful in every language.
 */
function readTitle(body: unknown): string | null {
  if (typeof body !== "object" || body === null) return null;
  const title = (body as Record<string, unknown>)["title"];
  return typeof title === "string" && title !== "" ? title : null;
}
