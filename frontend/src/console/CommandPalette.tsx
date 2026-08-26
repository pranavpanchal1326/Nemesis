"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { t, type Strings } from "@/lib/i18n/strings";
import { arrowAction, isTypingTarget, moveSelection, resolveShortcut, SHORTCUTS } from "./keyboard";
import { roadmapPhase, SCREENS, type Screen } from "./screens";
import "./console.css";

/**
 * `⌘K`, and the rest of the keyboard model with it — §E19, §E22.
 *
 * The palette is the only client component in the console shell, and it holds
 * the global key listener as well as the dialog because the two are one
 * feature: `/` has to know whether the palette is open before it decides
 * whether to focus the page's search field or the palette's own.
 *
 * **A native `<dialog>`, opened with `showModal()`.** Not a `<div
 * role="dialog">` with a hand-rolled focus trap. The platform element already
 * gives the three things a hand-rolled one gets wrong — focus is trapped, the
 * rest of the page is inert to assistive technology, and Escape closes it —
 * and every one of those is a §E22 requirement rather than a nicety. The
 * screen-reader pass in F3's gate is a pass over this element, and it is much
 * more likely to succeed on the element browsers implemented for it.
 *
 * **Filtering is a substring match over label and hint, and nothing cleverer.**
 * Fuzzy matching would rank "money" above "Review queue" for the query `re`
 * often enough to be a coin toss, and an officer who has learned that `⌘K rev
 * ⏎` reaches the queue must get the queue every time. A palette whose first
 * result moves is a palette people stop trusting and start reading.
 */
export function CommandPalette({ strings }: { readonly strings: Strings }) {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const listId = useId();
  const optionId = (index: number) => `${listId}-${String(index)}`;

  const matches = filterScreens(SCREENS, query, strings);

  const close = useCallback(() => {
    dialog.current?.close();
    setOpen(false);
  }, []);

  const show = useCallback(() => {
    setQuery("");
    setSelected(0);
    // `showModal` throws if the dialog is already open, and "already open" is
    // reachable — a second ⌘K arrives before React has re-rendered.
    if (dialog.current !== null && !dialog.current.open) dialog.current.showModal();
    setOpen(true);
    field.current?.focus();
  }, []);

  const go = useCallback(
    (screen: Screen) => {
      close();
      router.push(screen.href);
    },
    [close, router],
  );

  /**
   * The one global listener.
   *
   * On `document` rather than on a wrapper element, because §E22's promise is
   * that the console is operable from the keyboard *wherever focus happens to
   * be* — including on `<body>` itself, which is where focus sits after a
   * navigation and is precisely the moment an officer reaches for `j`.
   */
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const action = resolveShortcut({
        key: event.key,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        altKey: event.altKey,
        shiftKey: event.shiftKey,
        typing: isTypingTarget(event.target),
      });
      if (action === null) return;

      if (action === "palette") {
        event.preventDefault();
        if (open) close();
        else show();
        return;
      }

      // While the palette is open it owns the arrow keys, and only those: `e`
      // and `/` typed into the field are letters, which `isTypingTarget`
      // already decided above. This branch covers the case where focus is on
      // the dialog itself rather than in its field; the field handles its own,
      // for the reason given there.
      if (open) {
        if (action === "next" || action === "previous") {
          event.preventDefault();
          setSelected((current) => moveSelection(current, matches.length, action));
        }
        return;
      }

      if (action === "search") {
        // `/` focuses the screen's own search when it has one — the queue's
        // filter — and opens the palette when it does not, rather than doing
        // nothing. A shortcut that silently no-ops on half the screens is a
        // shortcut people stop pressing on all of them.
        const search = document.querySelector<HTMLElement>("[data-console-search]");
        event.preventDefault();
        if (search === null) show();
        else search.focus();
        return;
      }

      if (action === "help") {
        event.preventDefault();
        show();
        setQuery("?");
      }
      // `next`, `previous` and `evidence` outside the palette belong to
      // whichever list has focus; `useListKeys` handles them there. Falling
      // through rather than swallowing is what lets the queue own `j` without
      // this component knowing the queue exists.
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [close, matches.length, open, show]);

  const helping = query.trim() === "?";

  return (
    <>
      <button
        type="button"
        className="console__palette-open type-micro"
        onClick={show}
        aria-haspopup="dialog"
      >
        {t(strings, "palette.open")}
        <kbd className="console__kbd type-mono-data">{SHORTCUTS[0]?.keys ?? "K"}</kbd>
      </button>

      <dialog
        ref={dialog}
        className="palette"
        aria-label={t(strings, "palette.title")}
        onClose={() => {
          // Escape and the backdrop both close the element without going
          // through `close()`, so the state has to follow the DOM rather than
          // the other way round.
          setOpen(false);
        }}
      >
        <div className="palette__frame">
          <label className="palette__label type-micro" htmlFor={`${listId}-field`}>
            {t(strings, "palette.title")}
          </label>
          <input
            ref={field}
            id={`${listId}-field`}
            className="palette__field type-body"
            type="text"
            autoComplete="off"
            spellCheck={false}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={matches.length === 0 ? undefined : optionId(selected)}
            placeholder={t(strings, "palette.placeholder")}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setSelected(0);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                const screen = matches[selected];
                if (screen !== undefined) {
                  event.preventDefault();
                  go(screen);
                }
                return;
              }

              /*
               * The arrows are handled here and not by the global listener.
               *
               * `resolveShortcut` returns `null` for every single key while
               * `typing` is true, which is the rule that lets an officer spell
               * "Kajgaon" in a filter — and the palette's field is a text
               * field, so the global listener's `if (open)` branch never saw an
               * arrow and the highlight never moved. Relaxing the typing rule
               * for arrows globally would be the wrong repair: a policy rule in
               * §E19.8 is edited as a document, and a document editor whose
               * caret cannot move down is worse than a palette that needs six
               * lines here.
               */
              const action = arrowAction(event.key);
              if (action !== null) {
                event.preventDefault();
                setSelected((current) => moveSelection(current, matches.length, action));
              }
            }}
          />
          <p className="palette__hint type-caption">{t(strings, "palette.hint")}</p>

          {helping ? (
            <dl className="palette__keys">
              {SHORTCUTS.map((shortcut) => (
                <div key={shortcut.action} className="palette__key">
                  <dt>
                    <kbd className="console__kbd type-mono-data">{shortcut.keys}</kbd>
                  </dt>
                  <dd className="type-caption">{t(strings, shortcut.labelKey)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            /*
             * A `<div role="listbox">` rather than a `<ul>`.
             *
             * A list is a non-interactive element and giving it an interactive
             * role is an `axe` finding — assistive technology is told to expect
             * a listbox and finds list semantics underneath. The options are
             * `<button>` elements because they are activated, and the roving
             * selection is `aria-activedescendant` on the field above rather
             * than focus, which is what keeps the caret in the search box while
             * the arrow keys move the highlight.
             */
            <div
              className="palette__results"
              id={listId}
              role="listbox"
              aria-label={t(strings, "palette.results")}
            >
              {matches.map((screen, index) => (
                <button
                  key={screen.id}
                  type="button"
                  id={optionId(index)}
                  role="option"
                  aria-selected={index === selected}
                  className="palette__result"
                  tabIndex={-1}
                  onMouseEnter={() => {
                    setSelected(index);
                  }}
                  onClick={() => {
                    go(screen);
                  }}
                >
                  <span className="palette__result-name type-body">
                    {t(strings, `nav.${screen.id}`)}
                  </span>
                  <span className="palette__result-hint type-caption">
                    {t(strings, `nav.${screen.id}.hint`)}
                  </span>
                  {roadmapPhase(screen) === undefined ? null : (
                    <span className="palette__result-roadmap type-micro">
                      {t(strings, "palette.roadmap")}
                    </span>
                  )}
                </button>
              ))}
              {matches.length === 0 ? (
                <p className="palette__empty type-caption">{t(strings, "palette.empty")}</p>
              ) : null}
            </div>
          )}
        </div>
      </dialog>
    </>
  );
}

/**
 * Match on the *rendered* label, not on the id.
 *
 * An officer working in Marathi types Marathi. Filtering on `screen.id` would
 * give them a palette that only answers to English words, which is the same
 * defect as an untranslated string with an extra step — and it is the kind that
 * survives review, because everyone reviewing it types English.
 */
function filterScreens(
  screens: readonly Screen[],
  query: string,
  strings: Strings,
): readonly Screen[] {
  const needle = query.trim().toLocaleLowerCase(strings.locale);
  if (needle === "" || needle === "?") return screens;
  return screens.filter((screen) => {
    const haystack = `${t(strings, `nav.${screen.id}`)} ${t(strings, `nav.${screen.id}.hint`)}`;
    return haystack.toLocaleLowerCase(strings.locale).includes(needle);
  });
}
