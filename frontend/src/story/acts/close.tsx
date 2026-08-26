"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { useStore } from "zustand/react";

import { EVENT_SCORE_SCALE } from "@/lib/severity";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

import { actStyle } from "../acts";
import { storyLive } from "../live-story";

/**
 * Acts 6–8 — the merge, the city awake, the table (§E16, F14).
 *
 * **Act 6 is THE SHOT, and it is the one scene in this product that must not be
 * fakeable.** §E25's Phase 20 gate says it in one line: *"every scene is
 * triggered by a genuine backend event in an E2E test. A scene that can only be
 * fired by a button fails."* So the numbers on the stamp are the numbers
 * `cluster_match_found` actually carried — report count, match confidence,
 * distance, and the event's own timestamp — and when no merge has come through
 * the stream, the act says so and stamps nothing.
 *
 * That last sentence is the whole design. A landing page's strongest temptation
 * is to replay a recorded merge so the shot always lands, and doing it would
 * turn the film's most persuasive moment into its least honest one. §E3.3: an
 * honest empty state beats a confidently wrong screen — and on the marketing
 * surface, an *impressive* wrong screen.
 *
 * **The severity is printed only when it is about this report.** `clay/live.ts`
 * documents the join that does not exist: a merge happens to a cluster, a
 * severity is scored against a complaint, and no shaped payload carries a
 * cluster's severity. So the stamp has two forms — with the figure and without
 * it — exactly as `gate.dedup.merged` and `gate.dedup.mergedNoScore` already
 * do on the citizen surface. Printing the last severity that happened to
 * arrive would put a number on the most-photographed frame of the product that
 * is about a different report.
 *
 * **The rings are not decoration.** §E11.1: *"Absorbed reports leave faint
 * registration rings, because deduplication is not deletion and the visual must
 * say so."* One ring per absorbed report, counted from the merge's own report
 * count — so the picture and the number cannot disagree.
 */

/** §E16 Act 6 prints severity as a 0–1 figure; the event carries 0–10. */
function severityFigure(score: number): string {
  return (score / EVENT_SCORE_SCALE).toFixed(2);
}

export function TheMerge({ strings }: { readonly strings: Strings }) {
  const merge = useStore(storyLive, (state) => state.merge);
  const severity = useStore(storyLive, (state) => state.severity);
  const complaintId = useStore(storyLive, (state) => state.complaintId);

  // Only if it is about the report this film is following. See the module note.
  const figure =
    severity !== null && complaintId !== null && severity.entityId === complaintId
      ? severityFigure(severity.score)
      : null;

  const reports = merge?.reports ?? null;
  // One ring per report that was absorbed: the survivor is not a ring.
  const rings = reports === null ? 0 : Math.max(0, reports - 1);

  return (
    <section
      className="act"
      data-act="merge"
      data-real="true"
      data-merge={merge === null ? "waiting" : "live"}
      aria-labelledby="act-merge"
      style={actStyle("merge")}
    >
      <div className="act__panel merge">
        <h2 id="act-merge" className="type-micro">
          {t(strings, "story.act.merge")}
        </h2>

        {merge === null ? (
          <p className="merge__waiting type-body">{t(strings, "story.merge.waiting")}</p>
        ) : (
          <>
            <p className="type-title">{t(strings, "story.merge.heading")}</p>

            {/*
             * §E11.1 motion 2, in the paper layer. The 3D half is already
             * running: `clay/live.ts` put this cluster's pin into the
             * `merging` state off the same envelope, and the material bends it
             * toward the centroid on the same 900 ms curve. Two halves of one
             * movement, from one event.
             */}
            <ul className="merge__flags" data-motion="merge" aria-hidden="true">
              {Array.from({ length: Math.max(rings, 1) + 1 }, (_, index) => (
                <li
                  key={index}
                  className="merge__flag"
                  style={
                    {
                      "--flag-offset": `${String((index - 1) * 18)}px`,
                      "--flag-lean": `${String((index - 1) * -4)}deg`,
                    } as React.CSSProperties
                  }
                >
                  ▮
                </li>
              ))}
            </ul>

            <p className="merge__stamp type-doc" data-motion="stamp">
              {reports === null
                ? t(strings, "story.merge.stampNoSeverity", { reports: "—" })
                : figure === null
                  ? t(strings, "story.merge.stampNoSeverity", { reports })
                  : t(strings, "story.merge.stamp", { reports, severity: figure })}
            </p>

            {merge.confidence === null || merge.distanceMetres === null ? null : (
              <p className="type-mono-data">
                {t(strings, "story.merge.match", {
                  confidence: merge.confidence.toFixed(2),
                  distance: Math.round(merge.distanceMetres),
                })}
              </p>
            )}

            {/* §E11.1 — deduplication is not deletion, and the visual says so. */}
            <ul className="merge__rings" aria-hidden="true">
              {Array.from({ length: rings }, (_, index) => (
                <li key={index} className="merge__ring" />
              ))}
            </ul>
            <p className="type-micro">{t(strings, "story.merge.rings")}</p>

            <p className="merge__live type-body">{t(strings, "story.merge.live")}</p>
            {/*
             * §E16: *"with a mono timestamp ticking"* — the event's own
             * timestamp, verbatim, in the data face. Never `new Date()`: a
             * clock the browser drew would tick convincingly and mean nothing.
             */}
            <p className="merge__at type-mono-data">
              {t(strings, "story.merge.at", { timestamp: notTranslatable(merge.at) })}
            </p>
          </>
        )}
      </div>
    </section>
  );
}

/** A published place, as the film needs it — code and name, nothing else. */
export interface StoryZone {
  readonly code: string;
  readonly name: string;
}

export function TheCityAwake({
  strings,
  city,
  citySlug,
  zones,
}: {
  readonly strings: Strings;
  readonly city: string;
  readonly citySlug: string;
  /** Every place this city has published, from the same zone index §E18's ward
   *  pages are built on. Server-supplied, so Tier D gets the list too. */
  readonly zones: readonly StoryZone[];
}) {
  const listId = useId();
  const fieldId = useId();
  const [query, setQuery] = useState("");

  // A name typed in full resolves to its code; anything else resolves to
  // nothing and the link stays disabled. Deliberately not a fuzzy match: this
  // field opens a page of a city's real figures, and guessing which ward
  // somebody meant is not a guess this surface is entitled to make.
  const match = zones.find(
    (zone) =>
      zone.name.toLowerCase() === query.trim().toLowerCase() ||
      zone.code.toLowerCase() === query.trim().toLowerCase(),
  );

  return (
    <section
      className="act"
      data-act="city-awake"
      data-real="true"
      aria-labelledby="act-city"
      style={actStyle("city-awake")}
    >
      <div className="act__panel survey">
        <h2 id="act-city" className="type-title">
          {t(strings, "story.city.heading")}
        </h2>

        {/*
         * §E16: *"a survey frame rule-draws in — margins, scale bar, north
         * arrow, legend. The film has become a survey document, and the survey
         * document is the public dashboard."* The frame is drawn with the
         * Rule-Draw (§E11.1 motion 3), which is what a drafting table does and
         * the reason that motion exists at all.
         */}
        <hr className="rule-draw" data-motion="rule-draw" />
        <p className="type-micro">{t(strings, "story.city.surveyOf", { city })}</p>
        <div className="survey__marks type-micro" aria-hidden="true">
          <span>{t(strings, "story.city.north")}</span>
          <span>{t(strings, "story.city.scale")}</span>
          <span>{t(strings, "story.city.legend")}</span>
        </div>

        {/* §E16's three entrances, in its own order. */}
        <ul className="survey__entrances">
          <li>
            <Link className="survey__entrance type-heading" href="/report">
              {t(strings, "story.city.report")}
            </Link>
          </li>
          <li>
            <Link className="survey__entrance type-heading" href={`/${citySlug}`}>
              {t(strings, "story.city.ward")}
            </Link>
          </li>
          <li>
            {/* Quiet, per §E16 — an entrance for the people who already know
                it is there, not an invitation. */}
            <Link className="survey__entrance type-micro" href="/console">
              {t(strings, "story.city.signIn")}
            </Link>
          </li>
        </ul>

        <div className="survey__ward">
          <label className="type-micro" htmlFor={fieldId}>
            {t(strings, "story.city.wardField")}
          </label>
          <input
            id={fieldId}
            className="survey__field type-body"
            list={listId}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
            }}
          />
          {/* A `datalist` rather than a rendered dropdown: it is the platform's
              own combo box, it works with a keyboard and a screen reader
              without a line of ARIA, and — the reason that matters here — it is
              present in the server-rendered HTML, so Tier D readers get the
              whole list of a city's published places as options. */}
          <datalist id={listId}>
            {zones.map((zone) => (
              <option key={zone.code} value={zone.name} />
            ))}
          </datalist>
          {match === undefined ? null : (
            <Link
              className="survey__entrance type-micro"
              href={`/${citySlug}/ward/${match.code}`}
              data-ward={match.code}
            >
              {t(strings, "story.city.wardGo")}
            </Link>
          )}
        </div>
      </div>
    </section>
  );
}

export function TheTable({ strings }: { readonly strings: Strings }) {
  return (
    <section
      className="act"
      data-act="table"
      data-real="false"
      aria-labelledby="act-table"
      style={actStyle("table")}
    >
      <div className="act__panel bench">
        <h2 id="act-table" className="type-micro">
          {t(strings, "story.act.table")}
        </h2>
        {/*
         * §E16: *"The film's last frame is the console's establishing shot."*
         * The camera arrives at `WORLD.camera`'s own resting altitude in
         * `camera-keys.ts`, which is the shot the console and the public map
         * already draw — so the claim is made by the camera track rather than
         * by this sentence.
         */}
        <p className="type-body">{t(strings, "story.table.note")}</p>
      </div>
    </section>
  );
}
