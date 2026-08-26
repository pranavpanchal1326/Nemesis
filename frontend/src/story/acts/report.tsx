"use client";

import { useStore } from "zustand/react";

import { PipelineTheatre } from "@/citizen/PipelineTheatre";
import { ReportFlow } from "@/citizen/ReportFlow";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

import { actStyle } from "../acts";
import { storyLive } from "../live-story";

/**
 * Acts 4 and 5 — the report and the pipeline (§E16, F13).
 *
 * This is the hinge of the whole film, and it is where the direction's central
 * claim stops being a claim:
 *
 * > The camera pushes through the screen. **The viewfinder is the real
 * > `<ReportCapture>` in DOM** over a blurred backdrop.
 *
 * So Act 4 mounts `<ReportFlow>` — the component `/report` mounts, with the
 * same `<Viewfinder>`, the same `getUserMedia` path and the same optimistic
 * submit carrying the same idempotency key. Not a facsimile, not a video of
 * one, not a "demo mode" branch inside it. `tests/story.spec.ts` asserts it by
 * comparing what the film rendered against what the citizen route renders; a
 * copy would drift within a sprint and the assertion is what stops it.
 *
 * **Clay becomes paper here.** §E16: *"the 3D pothole freezes to a photograph,
 * and the photograph peels off the world into a paper card."* The peel is not a
 * decorative transition between two pictures of a card — the card it peels into
 * is the reader's own report, identified by the complaint id the 202 returned,
 * and it is the object Act 5 then stamps. A peel with nothing behind it would
 * be the exact thing §E3.1 rules out: a picture of evidence.
 *
 * **Act 5 is M5's theatre, unchanged.** §E17.2 already renders §E16.1's gates
 * from `GET /complaints/{id}/events` through `citizen/gates.ts`, including
 * §24.2's third outcome and the *held* state for a pipeline that will never
 * reach a gate. The film reuses it rather than re-implementing it, which is
 * what makes the Phase 20 gate — *"every scene is triggered by a genuine
 * backend event"* — true here by construction: there is no other code path.
 *
 * **What happens when nobody reports anything.** Most readers will scroll past
 * Act 4 without taking a photograph, and the film has to be honest about that.
 * It says so in a sentence and shows no stamps. The alternative — replaying a
 * canned run — is a scene fired by a scroll position, which is the *definition*
 * of the thing the gate fails.
 */

export function TheReport({
  strings,
  locale,
}: {
  readonly strings: Strings;
  readonly locale: string;
}) {
  const complaintId = useStore(storyLive, (state) => state.complaintId);
  const follow = useStore(storyLive, (state) => state.follow);

  return (
    <section
      className="act report-act"
      data-act="report"
      // Real from here on. The capture surface is the product's own, and what
      // it produces is a complaint in this deployment's event store.
      data-real="true"
      aria-labelledby="act-report"
      style={actStyle("report")}
    >
      <div className="act__panel">
        <h2 id="act-report" className="type-micro">
          {t(strings, "story.act.report")}
        </h2>

        <div className="report-act__phone" data-interactive="true">
          <ReportFlow
            strings={strings}
            locale={locale}
            // The film owns the document's `main`; this is the one caller that
            // needs the flow as a section. See `ReportFlow`'s own note.
            landmark="section"
            onComplaint={follow}
          />
        </div>

        <p className="report-act__note type-caption">{t(strings, "story.report.note")}</p>

        {/*
         * The peel — clay to paper. Rendered only once there is a real report
         * to peel *into*: the card carries the complaint's own id, in the data
         * face §E10.2 assigns to identifiers, and it is the same object Act 5
         * stamps immediately below.
         */}
        {complaintId === null ? null : (
          <div className="peel">
            <p className="peel__card type-mono-data">{notTranslatable(complaintId)}</p>
          </div>
        )}

        <p className="type-caption">{t(strings, "story.report.described")}</p>
      </div>
    </section>
  );
}

export function ThePipeline({ strings }: { readonly strings: Strings }) {
  const complaintId = useStore(storyLive, (state) => state.complaintId);

  return (
    <section
      className="act"
      data-act="pipeline"
      data-real="true"
      aria-labelledby="act-pipeline"
      style={actStyle("pipeline")}
    >
      <div className="act__panel gates">
        <h2 id="act-pipeline" className="type-micro">
          {t(strings, "story.act.pipeline")}
        </h2>

        {complaintId === null ? (
          <p className="gates__note type-body" data-gates="waiting">
            {t(strings, "story.pipeline.waiting")}
          </p>
        ) : (
          // The same component, the same reader, the same ledger as §E17.2.
          // Its `data-gate` / `data-state` attributes are what the E2E gate
          // reads, so the assertion is against the product rather than against
          // a copy of it made for the film.
          <PipelineTheatre complaintId={complaintId} strings={strings} />
        )}

        <p className="gates__note type-caption">{t(strings, "story.pipeline.note")}</p>
      </div>
    </section>
  );
}
