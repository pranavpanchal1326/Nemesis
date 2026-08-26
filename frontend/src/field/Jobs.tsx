"use client";

import { useEffect, useState } from "react";

import { NotWired } from "@/components/NotWired";
import { MILESTONE_STAGES } from "@/generated/enums";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

/**
 * The job list — §E21, and the honest half of F17.
 *
 * > **Three jobs on a list**, a large camera button, offline queue with visible
 * > per-item state. … Field staff **never see a kanban**.
 *
 * **Three, and never a board.** §E19.3 and §E21 agree on this and it is a
 * design decision rather than a screen size: a kanban asks somebody to *manage*
 * their work, and a person standing in a back lane with one glove off needs to
 * be told what to do next. So the list is short, ordered, and has no columns.
 *
 * **There is no work-order contract, and this screen says so rather than
 * inventing one.** `openapi.json` has no `WorkOrder` response schema — the same
 * gap `console/roadmap/Fixture.tsx` documents at length for F7's nine screens,
 * and Phase 14 is where it closes. Execution-plan Law 2 forbids building
 * against a shape the frontend invented, so what is drawn here is:
 *
 * · the **stage vocabulary**, from `generated/enums.ts` — a real closed set
 *   (§15.5's 30/40/30 gate strip), rendered as the row of stages a job moves
 *   through;
 * · the chip, naming the phase that populates it;
 * · and nothing else. No fixture job with a fixture address, because a list of
 *   three invented jobs on the surface whose whole purpose is *"do this next"*
 *   is the one screen in this product where a fixture would be indistinguishable
 *   from a lie.
 *
 * **What *is* real on this surface is everything below it**: the camera, the
 * compression, the outbox, and the sync. A field hand can capture evidence at
 * their own location today and it reaches the city; what they cannot yet do is
 * be *told which three jobs are theirs*, and that sentence is what this section
 * renders.
 */
export function Jobs({ strings }: { readonly strings: Strings }) {
  const [online, setOnline] = useState(true);

  useEffect(() => {
    const publish = (): void => {
      setOnline(navigator.onLine);
    };
    publish();
    window.addEventListener("online", publish);
    window.addEventListener("offline", publish);
    return () => {
      window.removeEventListener("online", publish);
      window.removeEventListener("offline", publish);
    };
  }, []);

  return (
    <section className="jobs" aria-labelledby="field-jobs">
      <h2 id="field-jobs" className="type-title">
        {t(strings, "field.jobs.title")}
      </h2>

      {/* The connection, stated. Not a toast and not an error: §E17.1's
          *"offline is not an error state"* applies with more force here, where
          being offline is the expected condition rather than the exception. */}
      <p className="jobs__link type-caption" data-online={String(online)}>
        {t(strings, online ? "field.online" : "field.offline")}
      </p>

      <p className="jobs__gap type-body">
        <NotWired phase="14" strings={strings} />
        {t(strings, "field.jobs.pending")}
      </p>

      {/* §15.5's stages are a real closed set, so they can be drawn. What they
          cannot be drawn *for* is a job that does not exist. */}
      <ol className="jobs__stages">
        {MILESTONE_STAGES.map((stage) => (
          <li key={stage} className="jobs__stage type-micro">
            {notTranslatable(stage)}
          </li>
        ))}
      </ol>
    </section>
  );
}
