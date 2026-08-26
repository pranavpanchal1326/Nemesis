"use client";

import { useSyncExternalStore } from "react";

import {
  densityServerSnapshot,
  densitySnapshot,
  DENSITY_MODES,
  isDensityMode,
  setDensity,
  subscribeDensity,
} from "@/lib/density";
import { t, type Strings } from "@/lib/i18n/strings";
import "./components.css";

/**
 * `<DensityControl>` — A10, §E19.
 *
 * §E19 asks for *"three density modes … persisted per user"*. The modes and the
 * persistence are in `src/lib/density.ts`; this is the affordance, and it is
 * three radio inputs rather than a button that cycles or a `<select>`.
 *
 * **Why radios.** All three options are visible, the current one is announced
 * as checked without the user having to open anything, and arrow keys move
 * between them because that is what a radio group does — §E19's keyboard model
 * asks for the console to be operable with no mouse, and a control that has to
 * be opened to be read is a control that fails that before it starts. A cycling
 * button would also make "which mode am I in" a question you answer by pressing
 * it, which is the §E3.3 problem in miniature.
 *
 * **It reads state rather than owning it.** The stored choice is applied to
 * `<html>` by a synchronous script before first paint (see `density.ts`), so by
 * the time this component mounts the document is already correct. It subscribes
 * to that module through `useSyncExternalStore` rather than holding its own
 * copy: the server renders the stylesheet's default because a server cannot
 * know a device preference, the client reads what the boot script wrote, and
 * two controls on one page cannot disagree about which mode is on.
 */
export function DensityControl({
  strings,
  className,
}: {
  readonly strings: Strings;
  readonly className?: string;
}) {
  const mode = useSyncExternalStore(subscribeDensity, densitySnapshot, densityServerSnapshot);

  return (
    <fieldset className={className === undefined ? "density" : `density ${className}`}>
      <legend className="type-micro">{t(strings, "density.legend")}</legend>
      {DENSITY_MODES.map((option) => (
        <label key={option} className="density__option type-caption">
          <input
            type="radio"
            name="density"
            value={option}
            checked={mode === option}
            onChange={(event) => {
              const next = event.target.value;
              if (!isDensityMode(next)) return;
              setDensity(next);
            }}
          />
          {t(strings, `density.${option}`)}
        </label>
      ))}
    </fieldset>
  );
}
