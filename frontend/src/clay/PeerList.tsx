import { Figure } from "@/public/Figure";
import { SeverityBadge } from "@/components/SeverityBadge";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import { entityDigest, levelOf, type ClayEntity } from "./entities";
import type { Tier } from "./tier";
import "./clay.css";

/**
 * The accessible peer list — M8.10, §E22, and a Phase 19 gate clause.
 *
 * > **The 3D map always has a synchronised accessible list view in the DOM — a
 * > peer, not a fallback, always present.**
 *
 * Three words in that sentence each rule out a design that would otherwise be
 * the obvious one.
 *
 * **"Always present"** rules out rendering this only when the canvas fails. A
 * list that appears when 3D is unavailable is a fallback, and a fallback is
 * built once, reviewed once, and rots — because nobody on the team ever sees
 * it. This renders in Tier S beside a working canvas, which means every visual
 * change to the map is made by somebody who is also looking at the list.
 *
 * **"Peer"** rules out `aria-hidden` on one and a `role="application"` on the
 * other. Both are the content. A sighted keyboard user tabbing through the list
 * moves the camera; a pointer user clicking a pin moves the list's selection.
 * Neither is the accessible copy of the other.
 *
 * **"Synchronised"** rules out building the list from a second query. It takes
 * the same `ClayEntity[]` the renderer instances, in the same order, and
 * publishes `entityDigest()` on its own root so a test can compare the two
 * strings in every tier. See `clay/entities.ts` for why that assertion is worth
 * more than a review comment.
 *
 * **Server-rendered.** No `"use client"` here: this is a plain list of text and
 * it must exist on a page whose JavaScript never ran (§E13 Tier D). The
 * interactive half — selection following the camera — is layered on by
 * `<ClayScene>`'s client half, and its absence costs a Tier D reader nothing
 * but the highlight.
 */
export function PeerList({
  entities,
  strings,
  tier,
  selectedId,
  onSelect,
  headingId,
}: {
  readonly entities: readonly ClayEntity[];
  readonly strings: Strings;
  readonly tier: Tier;
  readonly selectedId?: string | null;
  /** Supplied only by the client half. Absent on a server render, and the list
   *  is then a list of links rather than a list of buttons — which is the
   *  correct progressive answer, not a degraded one. */
  readonly onSelect?: (id: string) => void;
  readonly headingId: string;
}) {
  return (
    <section
      className="clay-peers"
      aria-labelledby={headingId}
      data-tier={tier}
      // The assertion seam. Not a debug attribute: `tests/clay.spec.ts` reads
      // this and the canvas's own copy of it and requires them to be equal in
      // every tier, which is how §E22's "synchronised" stops being a promise.
      data-clay-digest={entityDigest(entities)}
      data-clay-count={String(entities.length)}
    >
      <h2 id={headingId} className="type-micro clay-peers__title">
        {t(strings, "clay.title")}
      </h2>
      <p className="type-caption clay-peers__hint">{t(strings, "clay.listHint")}</p>

      {entities.length === 0 ? (
        // §E3.3 — an honest empty state, with the reason. Not a spinner and not
        // a zero: a map with nothing to draw is a fact about the published
        // data, and saying so is the whole product.
        <p className="type-body clay-peers__empty">{t(strings, "clay.empty")}</p>
      ) : (
        <ol className="clay-peers__list">
          {entities.map((entity) => (
            <PeerRow
              key={entity.id}
              entity={entity}
              strings={strings}
              selected={selectedId === entity.id}
              {...(onSelect === undefined ? {} : { onSelect })}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function PeerRow({
  entity,
  strings,
  selected,
  onSelect,
}: {
  readonly entity: ClayEntity;
  readonly strings: Strings;
  readonly selected: boolean;
  readonly onSelect?: (id: string) => void;
}) {
  const level = levelOf(entity);

  const body = (
    <>
      <span className="clay-peers__label type-body">{notTranslatable(entity.label)}</span>
      <SeverityBadge level={level} strings={strings} density="compact" />
      <span className="clay-peers__state type-caption">
        {t(strings, `clay.state.${entity.state}`)}
      </span>
      <span className="clay-peers__reports type-caption">
        {t(strings, "clay.reports")}{" "}
        {/* The figure goes through `<Figure>`, which is the only route a
            published count has to a screen (ADR-0021). A list beside a map is
            exactly where somebody would otherwise interpolate `zone.total`. */}
        <Figure figure={entity.reports} strings={strings} />
      </span>
    </>
  );

  return (
    <li
      className="clay-peers__row"
      data-entity={entity.id}
      data-severity={level ?? "unscored"}
      data-state={entity.state}
      aria-current={selected ? "true" : undefined}
    >
      {onSelect === undefined ? (
        entity.href === null ? (
          <span className="clay-peers__cell">{body}</span>
        ) : (
          <a className="clay-peers__cell" href={entity.href}>
            {body}
          </a>
        )
      ) : (
        <button
          type="button"
          className="clay-peers__cell"
          onClick={() => {
            onSelect(entity.id);
          }}
        >
          {body}
        </button>
      )}
    </li>
  );
}
