"use client";

import { useQuery } from "@tanstack/react-query";
import { useStore } from "zustand/react";

import { Stamp } from "@/components/Stamp";
import { fetchComplaintHistory, type ComplaintHistory } from "@/lib/api/complaints";
import { complaintHistoryKey, isPollingTransport, POLL_INTERVAL_MS } from "@/lib/api/queries";
import { t, type Strings } from "@/lib/i18n/strings";
import { realtimeStore } from "@/lib/realtime/store";

import { allGatesSettled, readGates, type Gate } from "./gates";
import "./citizen.css";

/**
 * §E17.2 — **the wait is the demo.**
 *
 * > §26.1 promises `estimated_processing_time_seconds: 8`. **Do not show a
 * > spinner.** Show the Act 5 gates, live, in clay-and-paper miniature, on the
 * > citizen's phone.
 *
 * Not a spinner, and the distinction is not decorative. A spinner says *"wait"*
 * and nothing else; these five rows say what is being checked, what each check
 * found, and — when a stage takes its fallback — that the system declined to
 * guess. §E3.1: *"a status badge is a claim; an event ledger is evidence"*, and
 * this is the ledger arriving in real time.
 *
 * **Where the facts come from.** `GET /complaints/{id}/events` — the
 * append-only log, disclosed per ADR-0043. Not the socket: §E14.3 makes the
 * socket a hint, and a stamp is a claim a citizen is asked to believe. The
 * socket's contribution is that `RealtimeProvider`'s reconciler refetches this
 * query the moment an envelope arrives for this entity, so the ledger updates
 * within a frame or two of the event rather than on the next poll.
 *
 * **The faster poll, and why it is bounded.** While gates are still waiting
 * this refetches every `THEATRE_POLL_MS` rather than on §27.3's five-second
 * fallback interval — the server itself budgets eight seconds for the whole
 * pipeline, and a five-second poll across an eight-second pipeline shows a
 * citizen roughly two frames of a five-act sequence. It stops the moment every
 * gate has settled, so the cost is bounded by the pipeline's own duration and
 * not by how long the tab stays open.
 *
 * **The clay-and-paper miniature is not here yet.** §E17.2 asks for the gates
 * as a rendered miniature; M8 owns the clay engine and it is not installed.
 * What ships is the *stamps* — real, driven by real events, on paper — and the
 * miniature arrives with M8 behind the same data. The gate this fails is
 * cosmetic; the gate it passes (*"every scene is triggered by a genuine backend
 * event"*) is the Phase 20 one.
 */

/** Roughly the 12 fps stepped clock's period × 14. Fast enough that a
 *  five-stage pipeline reads as a sequence; slow enough that eight seconds is
 *  ten requests and not eighty. */
export const THEATRE_POLL_MS = 1_200;

export function PipelineTheatre({
  complaintId,
  strings,
}: {
  readonly complaintId: string;
  readonly strings: Strings;
}) {
  const transport = useStore(realtimeStore, (state) => state.transport);

  const history = useQuery<ComplaintHistory>({
    queryKey: complaintHistoryKey(complaintId),
    queryFn: ({ signal }) => fetchComplaintHistory(complaintId, signal),
    refetchInterval: (query) => {
      const events = query.state.data?.events ?? [];
      if (allGatesSettled(readGates(events))) {
        // Settled. Back to whatever the transport says — nothing if the socket
        // is delivering, §27.3's five seconds if it is not. The ledger keeps
        // growing after the pipeline finishes (routing, assignment, closure),
        // so it does not stop being read; it stops being read *urgently*.
        return isPollingTransport(transport) ? POLL_INTERVAL_MS : false;
      }
      return THEATRE_POLL_MS;
    },
    refetchIntervalInBackground: false,
  });

  const gates = readGates(history.data?.events ?? []);

  return (
    <section className="theatre" aria-live="polite">
      <h2 className="theatre__title type-micro">{t(strings, "theatre.title")}</h2>
      <ol className="theatre__gates">
        {gates.map((gate) => (
          <GateRow key={gate.name} gate={gate} strings={strings} />
        ))}
      </ol>
      {allGatesSettled(gates) ? null : (
        <p className="theatre__note type-caption">{t(strings, "theatre.working")}</p>
      )}
      {/*
       * A report the pipeline stopped on says so, once, under the gates rather
       * than inside each of them. §24.2's card *continues* — and continuing,
       * for a parked report, means telling the person where it actually is
       * instead of leaving rows that will never change.
       */}
      {gates.some((gate) => gate.stampKey === "gate.held") ? (
        <p className="theatre__note type-caption" data-held="true">
          {t(strings, "theatre.held")}
        </p>
      ) : null}
    </section>
  );
}

function GateRow({ gate, strings }: { readonly gate: Gate; readonly strings: Strings }) {
  const label = t(strings, `gate.${gate.name}.label`);
  const stamp = t(strings, gate.stampKey, gate.vars);

  return (
    <li className="theatre__gate" data-gate={gate.name} data-state={gate.state}>
      <span className="theatre__gate-label type-micro">{label}</span>
      {gate.state === "waiting" ? (
        <span className="theatre__gate-stamp type-doc" data-waiting="true">
          {stamp}
        </span>
      ) : (
        /*
         * §E11.1 motion #1, and §E3.4's rule that the stamp means exactly one
         * thing: *a decision was made*. Every settled gate here is a decision
         * the pipeline made about this report — including the degraded one,
         * which is the decision **not** to guess. `animate` is on because these
         * are landing now, in front of the person they are about; the tracking
         * ledger replays the same decisions with `animate={false}`, because a
         * history should not re-enact itself on page load.
         */
        <Stamp kind={stampKindFor(gate)} label={stamp} />
      )}
    </li>
  );
}

/**
 * Which of §E26's five confirmation kinds a gate lands with.
 *
 * `accepted` for a normal pass, `verified` where the stage *checked* something
 * about the evidence. Deliberately never a sixth kind: §E11.1 closes the set,
 * and adding one is a decision about what counts as a decision.
 */
function stampKindFor(gate: Gate): "accepted" | "verified" {
  return gate.name === "trust" || gate.name === "redaction" ? "verified" : "accepted";
}
