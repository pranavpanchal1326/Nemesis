"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, useSyncExternalStore } from "react";

import { t, type Strings } from "@/lib/i18n/strings";

import { CUES } from "./cues";
import { sound, type SoundState } from "./graph";
import { followTheBus } from "./live-sound";
import "./sound.css";

/**
 * The unmute affordance — §E12, F16, and §E2 defect #9.
 *
 * > Muted by default, with an unmute affordance that is **designed rather than
 * > hidden**; state persists per user.
 *
 * "Designed rather than hidden" is the whole specification, and it rules out
 * the two things products usually do: autoplaying with a mute button, and
 * burying the toggle in a settings panel nobody opens. So this is a labelled
 * control in the page, it states what it will do before it does it, and it
 * states **why** it is silent when the reason is not the person's own choice.
 *
 * **Under `prefers-reduced-motion` the control renders and says so.** It is not
 * disabled and it is not removed: §E3.2 makes a degradation a designed edit
 * rather than an absence, and somebody who has asked their operating system for
 * reduced motion is still allowed to ask this product for sound. What the
 * preference changes is which buses run — see `busGainFor` — and the control
 * says that in a sentence rather than leaving a person to discover that half
 * the sound is missing.
 */

const SERVER_STATE: SoundState = { muted: true, reducedMotion: false, running: false };

export function SoundControl({ strings }: { readonly strings: Strings }) {
  const state = useSyncExternalStore(
    (listener) => sound.subscribe(listener),
    () => sound.state(),
    // The server has no `localStorage` and no `matchMedia`, and answering
    // anything but "muted, no preference known" here is a hydration mismatch on
    // a control whose only job is to state a fact.
    () => SERVER_STATE,
  );

  useEffect(() => {
    sound.hydrate();
  }, []);

  return (
    <div className="sound">
      <button
        type="button"
        className="sound__toggle type-micro"
        aria-pressed={!state.muted}
        onClick={() => {
          sound.setMuted(!state.muted);
        }}
      >
        {t(strings, state.muted ? "sound.unmute" : "sound.mute")}
      </button>
      {state.reducedMotion ? (
        <p className="sound__note type-caption">{t(strings, "sound.reducedMotion")}</p>
      ) : null}
    </div>
  );
}

/**
 * The sound layer, mounted once per surface that has one.
 *
 * Three responsibilities, and they are here rather than in the control because
 * a page can render the control twice — a console shell and a print header, say
 * — and must not therefore subscribe to the bus twice.
 *
 * · **Hydrate** the graph's two preferences.
 * · **Follow the bus**, so the merge cue and the fail-safe note play.
 * · **The page turn.** §E12 lists it as *"page turn on route change"*, and a
 *   route change in an app router is a pathname change and nothing else — there
 *   is no navigation event to listen for. Skipped on the *first* pathname,
 *   because arriving is not turning a page; a product that made a noise the
 *   instant it loaded would be violating its own muted-by-default rule the
 *   moment somebody turned the sound on.
 *
 *   **Every change after that plays, including a return to a route already
 *   visited.** The first version kept a set of seen pathnames and played only
 *   on a pathname it had not seen — which is *"page turn on first visit"*, a
 *   different and slightly odd sentence. An officer moving queue → policy →
 *   queue has turned two pages, and the second one is not silent.
 */
export function SoundProvider({ children }: { readonly children?: React.ReactNode }) {
  const pathname = usePathname();
  /** The pathname this provider last saw. `null` until it has seen one, which
   *  is the arrival rather than a turn. Per-instance rather than module-level,
   *  so two providers in one tree — which should not happen, but might — do not
   *  silence each other. */
  const previous = useRef<string | null>(null);

  useEffect(() => {
    sound.hydrate();
    return followTheBus();
  }, []);

  useEffect(() => {
    const before = previous.current;
    previous.current = pathname;
    if (before === null || before === pathname) return;
    sound.play("pageTurn", CUES.pageTurn.bus, CUES.pageTurn.recipe);
  }, [pathname]);

  return <>{children}</>;
}

/**
 * §E12's master duck, as a hook — *"a master duck on modal open"*.
 *
 * Called by whatever opens a modal, with the modal's own open state. Counted in
 * the graph rather than toggled, so two overlapping modals do not restore the
 * volume while one of them is still open.
 */
export function useModalDuck(open: boolean): void {
  useEffect(() => {
    if (!open) return;
    sound.pushModal();
    return () => {
      sound.popModal();
    };
  }, [open]);
}
