"use client";

import { EvidenceTrail, trailFromHistory } from "@/components/EvidenceTrail";
import { Receipt } from "@/components/Receipt";
import { StatusChip } from "@/components/StatusChip";
import type { ComplaintStatus } from "@/generated/enums";
import { COMPLAINT_STATUSES } from "@/generated/enums";
import { useComplaint, useComplaintHistory } from "@/lib/api/queries";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

import { DedupPayoff } from "./DedupPayoff";
import { SeverityWhy } from "./SeverityWhy";
import "./citizen.css";

/**
 * §E17.4 — **Track: a ledger, not a status badge.**
 *
 * > "In Progress" is the enemy; it is the exact failure the README opens with.
 * > Every state change is an event with an actor, a timestamp, and evidence,
 * > rendered as a vertical paper ledger.
 *
 * The status chip is at the top and it is **not** the answer. It is a summary
 * of the ledger below it, and the ledger is the record: every row is a real
 * event off the hash chain, in order, with the link that ties it to the one
 * before it. §E3.1 — *"a status badge is a claim; an event ledger is
 * evidence."*
 *
 * **Two reads, both authoritative, neither derived from the other.**
 * `GET /complaints/{id}` is the projection — what is true now — and
 * `GET /complaints/{id}/events` is the log. Both come through the BFF, both are
 * kept current by whichever transport is working, and the second one carries
 * `chain_head`, which is what the receipt in a citizen's hand is checked
 * against (ADR-0044).
 *
 * **The receipt is re-rendered here rather than only at submission.** §E17.3's
 * document is *"saveable, shareable"*, and a document you can only see once is
 * neither. It is the same component with the same chain hash — the live head
 * from the ledger, so a citizen who saved a receipt six weeks ago can compare
 * two strings and see that the record still says what it said.
 */
export function TrackScreen({
  complaintId,
  strings,
}: {
  readonly complaintId: string;
  readonly strings: Strings;
}) {
  const complaint = useComplaint(complaintId);
  const history = useComplaintHistory(complaintId);

  if (complaint.isError || history.isError) {
    return (
      <main className="track" data-state="unreadable">
        <h1 className="track__title type-title">{t(strings, "track.title")}</h1>
        <p className="track__error type-body">{t(strings, "track.unreadable")}</p>
      </main>
    );
  }

  const data = complaint.data;
  const events = history.data?.events ?? [];

  return (
    <main className="track">
      <h1 className="track__title type-title">{t(strings, "track.title")}</h1>

      <p className="track__id type-mono-data">{notTranslatable(complaintId)}</p>

      {data === undefined ? (
        <p className="track__loading type-body">{t(strings, "track.reading")}</p>
      ) : (
        <>
          <div className="track__status">
            <StatusChip status={asStatus(data.status)} strings={strings} explain />
          </div>

          <DedupPayoff complaint={data} strings={strings} />

          <SeverityWhy complaint={data} strings={strings} />
        </>
      )}

      <section className="track__ledger">
        <h2 className="track__ledger-title type-micro">{t(strings, "evidence.title")}</h2>
        {/*
         * `animate` is off inside the trail's own stamps for the reason §E26
         * gives: a ledger of past decisions should not re-enact all of them on
         * page load. The theatre animates because those decisions are landing
         * now, in front of the person they are about.
         */}
        <EvidenceTrail entries={trailFromHistory(events)} view="citizen" strings={strings} />
      </section>

      {history.data === undefined ? null : (
        <section className="track__receipt">
          <Receipt
            strings={strings}
            complaintId={complaintId}
            reportedAt={data?.reported_at ?? ""}
            chainHash={history.data.chain_head}
          />
          <p className="track__chain-note type-caption">
            {t(strings, "track.chainAt", { sequence: history.data.chain_head_sequence })}
          </p>
        </section>
      )}
    </main>
  );
}

/**
 * Narrow the read schema's `status: string` to the published enum.
 *
 * The backend types it `str` on the response model and publishes
 * `ComplaintStatus` as a separate component — so the union exists and the field
 * does not use it. Rather than cast, this checks against the generated runtime
 * array: a status the vocabulary does not contain falls back to `submitted`
 * *and the ledger below still shows exactly what happened*, which is the
 * behaviour §E26.1 wants from a chip it cannot render — never a blank, never a
 * crash, and never an invented state.
 */
function asStatus(value: string): ComplaintStatus {
  return (COMPLAINT_STATUSES as readonly string[]).includes(value)
    ? (value as ComplaintStatus)
    : "submitted";
}
