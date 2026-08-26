"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { Receipt } from "@/components/Receipt";
import {
  submitComplaint,
  type ApiError,
  type ComplaintDraft,
  type SubmissionOutcome,
} from "@/lib/api/complaints";
import { newIdempotencyKey } from "@/lib/api/idempotency";
import { complaintKey, useComplaint } from "@/lib/api/queries";
import { deviceFingerprint } from "@/lib/device";
import { t, type Strings } from "@/lib/i18n/strings";

import { DedupPayoff } from "./DedupPayoff";
import { PipelineTheatre } from "./PipelineTheatre";
import { PlaceCard, type Coordinates } from "./PlaceCard";
import { Viewfinder, type Capture } from "./Viewfinder";
import "./citizen.css";

/**
 * §E17.1's three screens, and §E17.2's wait — the citizen loop, end to end.
 *
 * **Send is optimistic and the word is load-bearing.** §E17.1 step 3:
 * *"Optimistic. Queued locally and confirmed instantly, before the round-trip.
 * Offline is not an error state."* So the moment the person commits, the flow
 * moves to `sent` and shows the theatre; the receipt appears when the 202
 * lands. What is optimistic here is the *acknowledgement*, never a fact — the
 * gates below it are read from the log and stay empty until the log says
 * otherwise, so the screen never claims something happened that did not.
 *
 * **The idempotency key is minted when the draft is, not when it is sent.**
 * That is the whole property (see `lib/api/idempotency.ts`): the first attempt,
 * the retry after a timeout, and M11's replay from IndexedDB three hours later
 * all carry the same key, and the server answers a repeat with the original
 * complaint and an `Idempotent-Replay` header rather than a second report.
 *
 * **A failed send keeps the draft.** The retry re-sends the *same* draft object
 * — same key, same photograph — which is what makes retrying safe. Discarding
 * the draft on failure would force a fresh key and turn one pothole into two
 * reports, which dedup would then merge, telling the citizen they are the
 * second person to report their own pothole.
 */

type Phase =
  /** §E17.1 step 1 — the viewfinder. */
  | { readonly kind: "capture" }
  /** §E17.1 step 2 — place, stated. */
  | { readonly kind: "place"; readonly capture: Capture }
  /** §E17.2 — sent, watching the gates. The id arrives with the 202. */
  | { readonly kind: "sent"; readonly draft: ComplaintDraft; readonly complaintId?: string }
  /** The send failed and the draft is intact. */
  | { readonly kind: "failed"; readonly draft: ComplaintDraft; readonly reason: string };

export function ReportFlow({
  strings,
  locale,
  landmark: Frame = "main",
  onComplaint,
}: {
  readonly strings: Strings;
  /** Carried on the submission so §8.4's transcription uses the right prompt
   *  set — a Marathi voice note scored against English prompts is the failure
   *  `language_uncertain` exists to record. */
  readonly locale: string;
  /**
   * Which element wraps the flow. `main` — the default, and what `/report`
   * uses — or `section`, for the one caller that embeds this inside a page
   * that already has a landmark: §E16 Act 4, where *"the viewfinder is the
   * real `<ReportCapture>` in DOM"* and the film owns the document's `main`.
   *
   * A prop rather than two components, because two components is two capture
   * flows and the whole claim of Act 4 is that there is one.
   */
  readonly landmark?: "main" | "section";
  /**
   * The complaint this flow produced, once the 202 has landed.
   *
   * Only the film supplies it: Acts 5 and 6 follow the reader's *own* report
   * through its own ledger, which is what makes those scenes fire on genuine
   * backend events rather than on a scroll position. `/report` ignores it —
   * the citizen surface already shows the theatre itself.
   */
  readonly onComplaint?: (complaintId: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: "capture" });
  const [at, setAt] = useState<Coordinates | null>(null);
  const [receipt, setReceipt] = useState<SubmissionOutcome | null>(null);
  const queryClient = useQueryClient();

  const send = useMutation<SubmissionOutcome, ApiError, ComplaintDraft>({
    mutationFn: (draft) => submitComplaint(draft),
    onSuccess: (outcome, draft) => {
      setReceipt(outcome);
      setPhase({ kind: "sent", draft, complaintId: outcome.receipt.complaint_id });
      onComplaint?.(outcome.receipt.complaint_id);
      // The projection exists the moment the 202 lands — `submit()` materialises
      // it in the same transaction as the event. Seeding the cache here means
      // the theatre's first render has a complaint rather than a spinner, and
      // the reconciler takes over from there.
      void queryClient.invalidateQueries({
        queryKey: complaintKey(outcome.receipt.complaint_id),
      });
    },
    onError: (error, draft) => {
      setPhase({ kind: "failed", draft, reason: error.message });
    },
    // No retry here. `ApiError.retriable` distinguishes a 429 from a 415, and
    // an automatic retry would hide that distinction from the person: they need
    // to know whether to wait or to take a different photograph.
    retry: false,
  });

  const beginSend = useCallback(
    (capture: Capture, coordinates: Coordinates) => {
      const draft: ComplaintDraft = {
        idempotencyKey: newIdempotencyKey(),
        latitude: coordinates.latitude,
        longitude: coordinates.longitude,
        deviceFingerprint: deviceFingerprint(),
        photo: capture.photo,
        audio: capture.audio,
        descriptionText: capture.description,
        locale,
      };
      // Optimistic: the phase moves before the request resolves.
      setPhase({ kind: "sent", draft });
      send.mutate(draft);
    },
    [locale, send],
  );

  if (phase.kind === "capture") {
    return (
      <Frame className="report" data-phase="capture">
        <Viewfinder
          strings={strings}
          onCapture={(capture) => {
            setPhase({ kind: "place", capture });
          }}
        />
      </Frame>
    );
  }

  if (phase.kind === "place") {
    const capture = phase.capture;
    return (
      <Frame className="report" data-phase="place">
        <PlaceCard strings={strings} value={at} onChange={setAt} />
        <button
          type="button"
          className="report__send type-heading"
          disabled={at === null}
          onClick={() => {
            if (at !== null) beginSend(capture, at);
          }}
        >
          {t(strings, "capture.send")}
        </button>
        <button
          type="button"
          className="report__back type-micro"
          onClick={() => {
            setPhase({ kind: "capture" });
          }}
        >
          {t(strings, "capture.back")}
        </button>
      </Frame>
    );
  }

  if (phase.kind === "failed") {
    const draft = phase.draft;
    return (
      <Frame className="report" data-phase="failed">
        <h1 className="report__title type-title">{t(strings, "send.failedTitle")}</h1>
        {/*
         * The server's own sentence, forwarded by the BFF. §25 strips the
         * upstream problem document's internals; what survives is the title,
         * which is the half written for a person.
         */}
        <p className="report__failure type-body">{phase.reason}</p>
        <p className="report__failure-note type-caption">{t(strings, "send.failedNote")}</p>
        <button
          type="button"
          className="report__send type-heading"
          onClick={() => {
            // The same draft, and therefore the same key. This is what makes
            // pressing it twice safe.
            setPhase({ kind: "sent", draft });
            send.mutate(draft);
          }}
        >
          {t(strings, "send.retry")}
        </button>
      </Frame>
    );
  }

  return (
    <Frame className="report" data-phase="sent">
      <SentScreen strings={strings} complaintId={phase.complaintId} outcome={receipt} />
    </Frame>
  );
}

/**
 * The wait, and then the receipt.
 *
 * Split out so `useComplaint` is called unconditionally: the complaint id only
 * exists once the 202 has landed, and a hook behind a conditional is a hook
 * React refuses. The query is disabled until there is an id, which is the same
 * thing expressed where the rules allow it.
 */
function SentScreen({
  strings,
  complaintId,
  outcome,
}: {
  readonly strings: Strings;
  readonly complaintId?: string | undefined;
  readonly outcome: SubmissionOutcome | null;
}) {
  const complaint = useComplaint(complaintId ?? "", complaintId !== undefined);

  return (
    <>
      <h1 className="report__title type-title">
        {t(strings, outcome?.replayed === true ? "send.alreadyFiled" : "send.filed")}
      </h1>

      {complaintId === undefined ? (
        // Optimistic, and honest about it: acknowledged locally, not yet
        // acknowledged by the city. §E17.1's "confirmed instantly, before the
        // round-trip" — with the round-trip's absence stated rather than
        // implied.
        <p className="report__pending type-body">{t(strings, "send.pending")}</p>
      ) : (
        <>
          <PipelineTheatre complaintId={complaintId} strings={strings} />

          {complaint.data === undefined ? null : (
            <DedupPayoff complaint={complaint.data} strings={strings} />
          )}

          {outcome === null ? null : (
            <section className="report__receipt">
              <Receipt
                strings={strings}
                complaintId={outcome.receipt.complaint_id}
                reportedAt={complaint.data?.reported_at ?? ""}
                chainHash={outcome.receipt.chain_hash}
              />
              <a className="report__track type-micro" href={`/t/${outcome.receipt.complaint_id}`}>
                {t(strings, "send.track")}
              </a>
            </section>
          )}
        </>
      )}
    </>
  );
}
