"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { EvidenceTrail, trailFromHistory } from "@/components/EvidenceTrail";
import { useComplaintHistory } from "@/lib/api/queries";
import { formatLedgerTime } from "@/lib/i18n/datetime";
import { notTranslatable, plural, t, type Strings } from "@/lib/i18n/strings";
import { isTypingTarget, moveSelection, resolveShortcut } from "@/console/keyboard";

import {
  isOpen,
  reasonKey,
  sortQueue,
  type DecisionKind,
  type QueueItem,
  type QueuePage,
} from "./queue";
import "./review.css";

/**
 * The review queue — §E19.1, §11.4, §E26, §E27. **REAL.**
 *
 * > the queue, the item, the decision … Media served through the redacted path
 * > only. `<EvidenceTrail>` in its officer filtering — *the same component as
 * > the citizen's, differing only by row filtering, never by different code.*
 *
 * Four decisions here are load-bearing.
 *
 * **The server rendered the first page; this owns it afterwards.** `initial`
 * arrives from `fetchQueue` so the screen exists before hydration, and
 * `useQuery` takes over with the same generated shape. `refetchInterval` is not
 * set: the socket tells us when something changed (§E14.3), and a queue that
 * also polled would move rows under a reviewer's cursor for no reason.
 *
 * **Selection is a row index, and the keyboard drives it.** `j` / `k` and the
 * arrow keys move it, `e` opens the evidence trail for whatever is selected,
 * `/` focuses the filter. `moveSelection` clamps rather than wraps, for the
 * reason its own docstring gives.
 *
 * **A decision is one judgement, forever, and the screen says so before and
 * after.** The rationale is required — the control is the server's, and this
 * refuses early so a reviewer is not told no after typing — and the button is
 * disabled while the write is in flight so a double-press cannot become a
 * second attempt at a write that is not idempotent.
 *
 * **Images come from the redacted path or not at all.** Every `src` is
 * `/api/review/media/{hash}`, which is the only image route in the product.
 */
export function ReviewQueue({
  strings,
  locale,
  initial,
}: {
  readonly strings: Strings;
  readonly locale: string;
  /** The server's first page, or `null` when the read failed. A failure is a
   *  state the screen renders, not an exception it throws. */
  readonly initial: QueuePage | null;
}) {
  const queryClient = useQueryClient();
  const [requested, setRequested] = useState(0);
  const [filter, setFilter] = useState("");
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const list = useRef<HTMLDivElement>(null);

  const queue = useQuery({
    queryKey: ["review", "queue"],
    queryFn: readQueue,
    ...(initial === null ? {} : { initialData: initial }),
  });

  const items = sortQueue((queue.data?.items ?? []).filter(isOpen)).filter((item) =>
    matches(item, filter, strings),
  );

  /**
   * The selection, clamped at render rather than corrected in an effect.
   *
   * A filter that removes the selected row must move the selection, or `e`
   * opens an item nobody can see. Doing that with a `setSelected` inside an
   * effect would render one frame with an out-of-range index and then render
   * again — a cascade React explicitly warns about, and one that would flash
   * the wrong item on a queue that narrows as the reviewer types.
   *
   * Clamping rather than resetting to zero keeps a reviewer near where they
   * were when they narrowed the list, and `requested` remembers where they
   * actually were — so widening the filter again returns them there instead of
   * to the top.
   */
  const selected = items.length === 0 ? 0 : Math.min(requested, items.length - 1);
  const current = items[selected];

  const onKeyDown = useCallback(
    (event: KeyboardEvent) => {
      const action = resolveShortcut({
        key: event.key,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        altKey: event.altKey,
        shiftKey: event.shiftKey,
        typing: isTypingTarget(event.target),
      });
      if (action === null) return;

      if (action === "next" || action === "previous") {
        event.preventDefault();
        setRequested((index) =>
          moveSelection(Math.min(index, items.length - 1), items.length, action),
        );
        return;
      }
      if (action === "evidence") {
        event.preventDefault();
        setEvidenceOpen((open) => !open);
      }
    },
    [items.length],
  );

  useEffect(() => {
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onKeyDown]);

  /**
   * Keeping the selected row in view is part of the keyboard path, not a
   * flourish: `j` twelve times on a forty-row queue is otherwise a selection
   * the reviewer cannot see.
   *
   * **It must not run on mount**, and that is not a micro-optimisation. Row
   * zero is already in view when the screen paints, so the scroll buys nothing
   * — and `scrollIntoView` sets the document's *sequential focus navigation
   * starting point* to the element it scrolled to. An officer's first `Tab` on
   * the command view then landed in the middle of the item pane instead of on
   * the skip link, which is the exact §E22 promise the skip link exists to
   * keep.
   *
   * The guard is *"the selection moved"*, held in a ref seeded with the
   * selection itself, rather than *"this is not the first run"*. A run counter
   * is wrong twice over: Strict Mode invokes an effect twice on mount and the
   * second invocation would scroll, and the reviewer may legitimately return to
   * row zero with `k` — a move that deserves the scroll as much as any other.
   */
  const shown = useRef(selected);
  useEffect(() => {
    if (shown.current === selected) return;
    shown.current = selected;
    const node = list.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    node?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  const decide = useMutation({
    mutationFn: (input: {
      readonly itemId: string;
      readonly decision: DecisionKind;
      readonly rationale: string;
    }) => recordDecision(input),
    onSuccess: () => {
      // The decided item leaves the open queue. Refetching rather than removing
      // it locally: the server decides what is open, and a screen that edited
      // its own copy would disagree with the next reader's.
      void queryClient.invalidateQueries({ queryKey: ["review", "queue"] });
    },
  });

  return (
    <div className="review">
      <section className="review__list-pane" aria-label={t(strings, "queue.label")}>
        <div className="review__filter">
          {/* Its own words. This label read *"Search screens and actions"* —
              the command palette's placeholder, borrowed — on the control that
              filters an officer's work list. A screen reader announced the
              wrong instruction on the console's busiest REAL screen. */}
          <label className="type-micro" htmlFor="review-filter">
            {t(strings, "queue.filter")}
          </label>
          <input
            id="review-filter"
            className="review__filter-field type-body"
            data-console-search=""
            type="search"
            autoComplete="off"
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value);
            }}
          />
        </div>

        <p className="review__sort type-micro">{t(strings, "sort.title")}</p>
        <p className="review__sort-why type-caption">{t(strings, "sort.why")}</p>

        {queue.isError && initial === null ? (
          <p className="review__empty type-caption">{t(strings, "queue.unavailable")}</p>
        ) : items.length === 0 ? (
          <p className="review__empty type-caption">{t(strings, "queue.empty")}</p>
        ) : (
          /*
           * A `<div role="listbox">` rather than a `<ul>`, for the reason the
           * palette gives: a list is a non-interactive element and giving it an
           * interactive role is an `axe` finding. The rows are `<button>`
           * elements — they are activated, and they must be reachable by the
           * pointer as well as by `j` / `k`.
           */
          <div
            ref={list}
            className="review__list"
            role="listbox"
            aria-label={t(strings, "queue.label")}
            tabIndex={0}
          >
            {items.map((item, index) => (
              <button
                key={item.id}
                type="button"
                role="option"
                aria-selected={index === selected}
                className="review__row"
                tabIndex={-1}
                onClick={() => {
                  setRequested(index);
                }}
              >
                <span className="review__row-reason type-caption">
                  {t(strings, reasonKey(item.reason))}
                </span>
                <span className="review__row-id type-mono-data">
                  {notTranslatable(shortId(item.id))}
                </span>
                <span className="review__row-age type-mono-data">
                  {formatLedgerTime(item.created_at, locale)}
                </span>
                <span className="review__row-priority type-mono-data">
                  {notTranslatable(String(item.priority))}
                </span>
              </button>
            ))}
          </div>
        )}

        <p className="review__count type-caption">
          {plural(strings, "queue.count", items.length, { count: items.length })}
        </p>
      </section>

      <section className="review__item-pane" aria-label={t(strings, "console.screen")}>
        {current === undefined ? (
          <p className="type-caption">{t(strings, "item.none")}</p>
        ) : (
          <ReviewItemPanel
            item={current}
            strings={strings}
            locale={locale}
            evidenceOpen={evidenceOpen}
            onToggleEvidence={() => {
              setEvidenceOpen((open) => !open);
            }}
            onDecide={(decision, rationale) => {
              decide.mutate({ itemId: current.id, decision, rationale });
            }}
            pending={decide.isPending}
            failure={decide.isError ? decide.error.message : null}
            decided={decide.isSuccess}
          />
        )}
      </section>
    </div>
  );
}

/** One item, its evidence, and the decision. */
function ReviewItemPanel({
  item,
  strings,
  locale,
  evidenceOpen,
  onToggleEvidence,
  onDecide,
  pending,
  failure,
  decided,
}: {
  readonly item: QueueItem;
  readonly strings: Strings;
  readonly locale: string;
  readonly evidenceOpen: boolean;
  readonly onToggleEvidence: () => void;
  readonly onDecide: (decision: DecisionKind, rationale: string) => void;
  readonly pending: boolean;
  readonly failure: string | null;
  readonly decided: boolean;
}) {
  const [rationale, setRationale] = useState("");
  const history = useComplaintHistory(item.complaint_id);

  return (
    <article className="review__item">
      <h2 className="type-heading">{t(strings, "item.title", { id: shortId(item.id) })}</h2>

      <dl className="review__facts">
        <Fact label={t(strings, "queue.reason")} value={t(strings, reasonKey(item.reason))} />
        <Fact label={t(strings, "item.priority")} value={notTranslatable(String(item.priority))} />
        <Fact
          label={t(strings, "item.trust")}
          value={notTranslatable(item.trust_score.toFixed(2))}
        />
        <Fact
          label={t(strings, "item.occurrences")}
          value={notTranslatable(String(item.occurrences))}
        />
        <Fact label={t(strings, "queue.filed")} value={formatLedgerTime(item.created_at, locale)} />
      </dl>

      <section className="review__media" aria-label={t(strings, "item.media")}>
        <h3 className="type-micro">{t(strings, "item.media")}</h3>
        <p className="type-caption">{t(strings, "item.mediaNote")}</p>
        {item.redacted_media.length === 0 ? (
          <p className="type-caption">{t(strings, "item.noMedia")}</p>
        ) : (
          <ul className="review__thumbs">
            {item.redacted_media.map((hash) => (
              <li key={hash}>
                {/*
                  `next/image` is deliberately not used. It proxies through the
                  optimiser, which would put a second copy of a redacted
                  photograph in a build cache on disk — and the one rule this
                  screen has is that the redacted path is the only path.
                */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  className="review__thumb"
                  src={`/api/review/media/${hash}`}
                  alt={t(strings, "item.media")}
                  width={160}
                  height={120}
                  loading="lazy"
                />
                <span className="type-mono-data">{notTranslatable(shortId(hash))}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="review__evidence" aria-label={t(strings, "item.evidence")}>
        <button type="button" className="review__toggle type-micro" onClick={onToggleEvidence}>
          {t(strings, evidenceOpen ? "evidence.close" : "evidence.title")}
          <kbd className="console__kbd type-mono-data">e</kbd>
        </button>
        {!evidenceOpen ? null : history.data === undefined ? (
          <p className="type-caption">{t(strings, "state.loading")}</p>
        ) : (
          /*
           * §E26's contract, and F4's gate: *the same component as the
           * citizen's, differing only by row filtering, never by different
           * code.* `view="officer"` is the only difference between this call
           * and the one on `/t/[id]`.
           */
          <EvidenceTrail
            entries={trailFromHistory(history.data.events)}
            view="officer"
            strings={strings}
          />
        )}
      </section>

      <form
        className="review__decision"
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <fieldset disabled={pending || decided}>
          <legend className="type-micro">{t(strings, "decision.legend")}</legend>
          <label className="type-caption" htmlFor="review-rationale">
            {t(strings, "decision.note")}
          </label>
          <textarea
            id="review-rationale"
            className="review__rationale type-body"
            rows={3}
            placeholder={t(strings, "decision.notePlaceholder")}
            value={rationale}
            onChange={(event) => {
              setRationale(event.target.value);
            }}
          />
          <p className="type-caption">{t(strings, "decision.escalate.why")}</p>
          <div className="review__actions">
            {(["approve", "reject", "escalate"] as const).map((kind) => (
              <button
                key={kind}
                type="submit"
                className="review__action type-caption"
                // Disabled without a reason, because a decision nobody can
                // review later is not a decision. The server refuses it too,
                // and the server is the control (§E19.4).
                disabled={rationale.trim() === ""}
                onClick={() => {
                  onDecide(kind, rationale);
                }}
              >
                {t(strings, `decision.${kind}`)}
              </button>
            ))}
          </div>
        </fieldset>

        <p className="review__decision-state type-caption" role="status">
          {pending
            ? t(strings, "decision.recording")
            : decided
              ? t(strings, "decision.recorded")
              : failure === null
                ? null
                : t(strings, "decision.failed")}
        </p>
      </form>
    </article>
  );
}

function Fact({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div className="review__fact">
      <dt className="type-micro">{label}</dt>
      <dd className="type-mono-data">{value}</dd>
    </div>
  );
}

/** A uuid is 36 characters and a queue row is one line. The first segment is
 *  what an officer reads aloud; the whole value stays in the DOM on the button
 *  so a copy still yields the record's own id. */
function shortId(id: string): string {
  return id.split("-")[0] ?? id;
}

/** Substring over the words the row shows, for the same reason the palette
 *  filters on rendered labels rather than ids: an officer working in Marathi
 *  types Marathi. */
function matches(item: QueueItem, filter: string, strings: Strings): boolean {
  const needle = filter.trim().toLocaleLowerCase(strings.locale);
  if (needle === "") return true;
  const haystack = `${t(strings, reasonKey(item.reason))} ${item.id} ${item.complaint_id}`;
  return haystack.toLocaleLowerCase(strings.locale).includes(needle);
}

async function readQueue(): Promise<QueuePage> {
  const response = await fetch("/api/review/queue", { cache: "no-store" });
  if (!response.ok) throw new Error("queue");
  return (await response.json()) as QueuePage;
}

async function recordDecision(input: {
  readonly itemId: string;
  readonly decision: DecisionKind;
  readonly rationale: string;
}): Promise<void> {
  const response = await fetch(`/api/review/queue/${input.itemId}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision: input.decision, rationale: input.rationale }),
  });
  if (!response.ok) throw new Error("decision");
}
