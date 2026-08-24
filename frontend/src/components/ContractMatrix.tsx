import {
  COMPLAINT_STATUSES,
  WORK_ORDER_STATUSES,
  type ComplaintStatus,
  type WorkOrderStatus,
} from "@/generated/enums";
import { PAPER, SEVERITY_DESCENDING } from "@/design/generated/tokens";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import type { RealtimeEnvelope } from "@/lib/realtime/envelope";

import { BeforeAfter } from "./BeforeAfter";
import { ContractorLedger } from "./ContractorLedger";
import { DegradedBanner } from "./DegradedBanner";
import { EvidenceTrail } from "./EvidenceTrail";
import { FlaggedNotice } from "./FlaggedNotice";
import { NotWired } from "./NotWired";
import { Receipt } from "./Receipt";
import { SeverityBadge } from "./SeverityBadge";
import { Stamp } from "./Stamp";
import { StatusChip, WorkOrderChip } from "./StatusChip";
import { SuppressionNotice } from "./SuppressionNotice";
import "./components.css";

/**
 * Every §E26 contract, in one place.
 *
 * §E24 asks for *"Storybook for every component across three densities × two
 * themes × two scripts"*. This is the content of that matrix, factored out so
 * the same rendering serves three consumers and cannot diverge between them:
 * the dev proof route, the `axe` sweep, and the golden-image regression.
 *
 * **It renders every member of every enumeration, on purpose.** §E26.1 calls a
 * view that omits a status a defect, and the two states most likely to be
 * forgotten are the two that only occur when something has gone wrong. A matrix
 * that showed a representative sample would be a matrix that never showed
 * `pending_classification` or `flagged` — which is exactly how they end up
 * unstyled, in production, on the day the classifier goes down.
 */

export interface ContractMatrixProps {
  readonly strings: Strings;
}

export function ContractMatrix({ strings }: ContractMatrixProps) {
  return (
    <div className="contract-matrix">
      <Section title="Severity — ink, shape, label (§E9.4)">
        <div className="contract-matrix__row">
          {SEVERITY_DESCENDING.map((level) => (
            <SeverityBadge key={level} level={level} strings={strings} score={0.78} />
          ))}
          {/* Unscored is a state, not a level. §E28 marks the severity panel
              SIMULATED until Phase 12, and this is what null must look like. */}
          <SeverityBadge level={null} strings={strings} />
        </div>
      </Section>

      <Section title="Complaint status — all thirteen (§E26.1)">
        <div className="contract-matrix__row">
          {COMPLAINT_STATUSES.map((status: ComplaintStatus) => (
            <StatusChip key={status} status={status} strings={strings} />
          ))}
        </div>
        <div className="contract-matrix__row">
          <StatusChip status="pending_classification" strings={strings} explain />
          <StatusChip status="flagged" strings={strings} explain />
        </div>
      </Section>

      <Section title="Work order status — all six, including created and disputed (§E26.1)">
        <div className="contract-matrix__row">
          {WORK_ORDER_STATUSES.map((status: WorkOrderStatus) => (
            <WorkOrderChip key={status} status={status} strings={strings} />
          ))}
        </div>
      </Section>

      <Section title="Flagged — hatch, disclaimer, response (ADR-0039, §16.4)">
        <FlaggedNotice
          strings={strings}
          disclaimer={notTranslatable(
            "This is an automated signal, not a finding. It has not been reviewed and no decision has been taken.",
          )}
          responseHref="#response"
          appeal="pending"
          detector={{
            name: notTranslatable("award-concentration"),
            threshold: notTranslatable("threshold 0.55"),
            confidence: notTranslatable("confidence 0.61"),
          }}
        />
      </Section>

      <Section title="Suppression — never a blank cell (ADR-0021)">
        <SuppressionNotice threshold={5} strings={strings} explain />
      </Section>

      <Section title="Degradation — calm, named, never an error colour (§E26)">
        <DegradedBanner
          strings={strings}
          cause={notTranslatable(
            "Live updates are switched off on this deployment. Showing the latest saved state instead, refreshed every few seconds.",
          )}
          since={new Date("2026-08-24T09:14:00Z")}
        />
      </Section>

      <Section title="The stamp — the one confirmation primitive (§E11.1)">
        <div className="contract-matrix__row">
          {(["accepted", "verified", "confirmed", "activated", "approved"] as const).map((kind) => (
            <Stamp key={kind} kind={kind} label={t(strings, `stamp.${kind}`)} animate={false} />
          ))}
        </div>
      </Section>

      <Section title="Evidence trail — one renderer, three filters (§E26)">
        <div className="contract-matrix__columns">
          {(["citizen", "officer", "public"] as const).map((view) => (
            <div key={view}>
              <p className="type-micro">{notTranslatable(view)}</p>
              <EvidenceTrail entries={SAMPLE_TRAIL} view={view} strings={strings} />
            </div>
          ))}
        </div>
      </Section>

      <Section title="Receipt — a document, not a toast (§E17.3)">
        <Receipt
          strings={strings}
          complaintId="9f2c41ab-7d3e-4c19-b0aa-1e5f2c9d4471"
          reportedAt="2026-08-24T11:04:19Z"
        />
      </Section>

      <Section title="Contractor ledger — four metrics, no single score (§16.1)">
        <ContractorLedger
          strings={strings}
          metrics={{
            onTimeRate: 0.72,
            costVariance: null,
            confirmedCount: 41,
            disputedCount: 6,
            repeatDefectRate: null,
          }}
        />
      </Section>

      <Section title="Before / after — identical on all three surfaces (§E26)">
        <div className="contract-matrix__columns">
          <BeforeAfter
            strings={strings}
            before={{ src: PLACEHOLDER_BEFORE, capturedAt: "2026-06-02T08:11:00Z" }}
            after={{ src: PLACEHOLDER_AFTER, capturedAt: "2026-07-18T16:40:00Z" }}
            ssim={0.81}
          />
          {/* The band where SSIM is not deciding, and neither does the label. */}
          <BeforeAfter
            strings={strings}
            before={{ src: PLACEHOLDER_BEFORE }}
            after={{ src: PLACEHOLDER_AFTER }}
            ssim={0.53}
          />
        </div>
      </Section>

      <Section title="Not wired — dev-only, never a public URL (§E24)">
        <NotWired phase="Phase 14" strings={strings} />
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="contract-matrix__section">
      <h2 className="type-caption contract-matrix__heading">{title}</h2>
      {children}
    </section>
  );
}

/**
 * Envelopes, not an invented row type — the same shape `/ws/pipeline-events`
 * delivers and the same shape a history endpoint replaying the log would return
 * (execution-plan Law 2, and defect #18 for why that endpoint is still owed).
 */
const SAMPLE_TRAIL: readonly RealtimeEnvelope[] = [
  envelope(1, "complaint_submitted"),
  envelope(2, "exif_check_completed"),
  envelope(3, "classification_scored"),
  envelope(4, "media_redacted"),
  envelope(5, "cluster_match_found"),
  envelope(6, "severity_scored"),
  envelope(7, "review_queued"),
  envelope(8, "work_order_assigned"),
  envelope(9, "citizen_confirmed"),
];

function envelope(sequence: number, eventType: string): RealtimeEnvelope {
  return {
    event_type: eventType,
    entity_type: "complaint",
    entity_id: "9f2c41ab-7d3e-4c19-b0aa-1e5f2c9d4471",
    sequence,
    timestamp: `2026-08-24T11:0${String(sequence)}:00Z`,
    cursor: sequence,
    payload: {},
  };
}

/**
 * Flat SVG stand-ins, inline as data URIs.
 *
 * A matrix that reached for a photograph would need one committed, and §6
 * Principle #6 forbids fetching one. Flat fields also make a compositing
 * failure legible: if the clip and the divider ever disagree, it shows here
 * before it shows on somebody's closure evidence.
 */
function flatField(stock: string): string {
  return (
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300">` +
        `<rect width="400" height="300" fill="${stock}"/></svg>`,
    )
  );
}

// From the token file, like everything else. Even a placeholder is not licensed
// to invent a colour — §E24, and `check-guards.ts` would fail the build if it
// tried.
const PLACEHOLDER_BEFORE = flatField(PAPER["mitti-500"]);
const PLACEHOLDER_AFTER = flatField(PAPER["kraft-200"]);
