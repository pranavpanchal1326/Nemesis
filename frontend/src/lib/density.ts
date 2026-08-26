import { DENSITY, type DensityMode } from "@/design/generated/tokens";

/**
 * Density, persisted per user — A10, §E19.
 *
 * > *"three density modes … persisted per user"* — §E19
 *
 * The three modes have existed as tokens and CSS since M1 and are exercised by
 * the §E26 contract matrix. What was missing is the sentence's second half:
 * nothing stored a choice and nothing offered one, so *"an officer sets it once
 * per page load, which is not setting it"*.
 *
 * **Restored before first paint, not in an effect.** `useEffect` runs after
 * hydration, which is after the browser has painted — an officer who chose
 * `dense` would watch a `compact` console appear and then reflow, on every
 * navigation, for the life of the deployment. So the stored value is applied by
 * a synchronous script in the document (see `DENSITY_BOOT_SCRIPT`), and React
 * reads back what that script already wrote.
 *
 * **`localStorage`, not a cookie and not the server.** The choice is a property
 * of *this machine* — a shared municipal terminal in a ward office and an
 * officer's own laptop want different answers, and the same person at both
 * wants each remembered separately. It is also not worth a round trip: nothing
 * about it is a fact the platform needs to hold, and §E14.1's BFF exists for
 * facts the platform holds.
 */

export const DENSITY_MODES = Object.keys(DENSITY) as readonly DensityMode[];

/** The default the stylesheet already encodes: `:root` *is* `compact`. */
export const DEFAULT_DENSITY: DensityMode = "compact";

/** Namespaced, because a municipal deployment may share an origin with
 *  something else somebody installed. */
export const DENSITY_STORAGE_KEY = "nemesis.density";

/** The attribute `tokens.css` selects on. Named once, used by the boot script,
 *  the control and the test, so a rename cannot half-land. */
export const DENSITY_ATTRIBUTE = "data-density";

export function isDensityMode(value: unknown): value is DensityMode {
  return typeof value === "string" && (DENSITY_MODES as readonly string[]).includes(value);
}

/** Read the stored choice, or the default.
 *
 *  The `try` is doing two jobs, both real: on a server `globalThis.localStorage`
 *  is undefined, and in a locked-down browser profile — a municipal terminal
 *  with storage denied is a deployment, not a hypothetical — merely touching it
 *  throws. Either way the answer is the default, not an exception. */
export function storedDensity(): DensityMode {
  try {
    const raw = globalThis.localStorage.getItem(DENSITY_STORAGE_KEY);
    return isDensityMode(raw) ? raw : DEFAULT_DENSITY;
  } catch {
    return DEFAULT_DENSITY;
  }
}

/**
 * The store React reads through `useSyncExternalStore`.
 *
 * A module-level value rather than component state, for two reasons. The
 * snapshot has to be *stable* between renders or React re-renders forever, and
 * the choice is one fact about the document — two controls on one page (the
 * shell's and a settings screen's, once F3 has both) must not be able to
 * disagree about which mode is on.
 */
let current: DensityMode | null = null;
const listeners = new Set<() => void>();

export function subscribeDensity(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
  };
}

/** What the boot script already applied, read once and then remembered. */
export function densitySnapshot(): DensityMode {
  current ??= storedDensity();
  return current;
}

/** The server renders the stylesheet's own default, because a server cannot
 *  know a preference that lives on a device. `<html suppressHydrationWarning>`
 *  is what makes the boot script's disagreement legitimate rather than a
 *  hydration error. */
export function densityServerSnapshot(): DensityMode {
  return DEFAULT_DENSITY;
}

/** Apply, persist, notify. Applying without persisting would be a control that
 *  forgets; persisting without applying would be a control that does nothing
 *  until you reload. */
export function setDensity(mode: DensityMode): void {
  current = mode;
  document.documentElement.setAttribute(DENSITY_ATTRIBUTE, mode);
  try {
    globalThis.localStorage.setItem(DENSITY_STORAGE_KEY, mode);
  } catch {
    // A profile with storage denied still gets the density it asked for, for
    // as long as the tab lives. Failing the interaction because the preference
    // cannot outlive it would be the worse trade.
  }
  for (const listener of listeners) listener();
}

/**
 * The pre-paint script, as source.
 *
 * Deliberately tiny and deliberately not imported from the module above: this
 * string is inlined into the document and runs before any bundle is fetched,
 * so it can share constants at *build* time (through the template below) but
 * not code at run time.
 *
 * It is wrapped in a `try` because a blocked `localStorage` throws on access in
 * some configurations, and an exception here would happen before hydration —
 * a blank page, caused by a preference.
 */
export const DENSITY_BOOT_SCRIPT = `(function(){try{var m=localStorage.getItem(${JSON.stringify(
  DENSITY_STORAGE_KEY,
)});if(${JSON.stringify(DENSITY_MODES)}.indexOf(m)>-1){document.documentElement.setAttribute(${JSON.stringify(
  DENSITY_ATTRIBUTE,
)},m);}}catch(e){}})();`;
