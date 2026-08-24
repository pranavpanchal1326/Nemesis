import { t, type Strings, type Translated } from "@/lib/i18n/strings";
import "./components.css";

/**
 * `<DegradedBanner>` — §E26, §E13, §E14.3, §24.2.
 *
 * > Named degradation with an honest cause. **Calm register, secondary ink,
 * > never an error colour.**
 *
 * Three sources feed this one component, and they are deliberately not three
 * components: `pipeline_stage_degraded` and `system_degradation` from the event
 * stream, a refused WebSocket upgrade (§E14.3), and a second WebGL context loss
 * dropping permanently to Tier C (§E13). A reader should not have to learn
 * three visual languages for "something is running in a reduced mode".
 *
 * **Why it is never an error colour.** A degradation is the system working as
 * designed — §24.2's whole point is that the pipeline parks a report rather
 * than guessing, and §E13's whole point is that a reduced tier reads as a
 * bolder print rather than a worse one. Painting either red would teach an
 * operator that a correct fallback is a fault, and the next time it happens
 * they would escalate instead of continuing.
 */
export function DegradedBanner({
  cause,
  since,
  strings,
}: {
  /** What is reduced, in words a person can act on. Not a status code. */
  readonly cause: Translated;
  readonly since?: Date;
  readonly strings: Strings;
}) {
  return (
    <div className="degraded-banner" role="status">
      <p className="degraded-banner__title type-micro">{t(strings, "degraded.title")}</p>
      <p className="degraded-banner__cause type-caption">{cause}</p>
      {since === undefined ? null : (
        <p className="degraded-banner__since type-mono-data">
          {t(strings, "degraded.since", { time: since.toISOString() })}
        </p>
      )}
    </div>
  );
}
