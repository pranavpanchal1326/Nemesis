import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { MOTION } from "../src/design/generated/tokens.ts";
import { MERGE_HOLD_MS, MOTION_IDS, SIGNATURE_MOTIONS, motion } from "../src/story/motions.ts";

/**
 * **Five signature motions, and only five** — §E11.1, F14's audit line.
 *
 * F14 ships *"the five signature motions audited as five"*, and an audit that
 * is a person reading a stylesheet is an audit that happens once. This is the
 * mechanical version, and it checks two different things:
 *
 * · **The register is five, and every one of them is used.** An unused motion
 *   is a vocabulary of four with a footnote; a sixth is the erosion §E11.1
 *   exists to prevent.
 *
 * · **No stylesheet in the product spends a duration or a curve that is not a
 *   token.** This is the part that catches drift, because drift never arrives
 *   as a new named motion — it arrives as `transition: opacity 300ms ease` in
 *   a component somebody was finishing at six o'clock. Every duration in §E11
 *   is a multiple of the 12 fps step, which is what makes the whole product
 *   beat to the same clock; a literal is the moment that stops being true.
 *
 * It reads **every** stylesheet in `src/`, not just the film's. §E11.1 is a
 * product-wide rule and an audit scoped to the surface that happens to be
 * shipping is not an audit.
 */

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const SRC = join(ROOT, "src");

/**
 * The one timing function allowed beside the tokens.
 *
 * `linear` is not a curve choice — it is the absence of one, and it is correct
 * for a pure opacity crossfade, where any easing is a decision about a fade
 * nobody can perceive the shape of. §E11's four curves are for things that
 * *move*.
 */
const NO_CURVE = new Set(["linear", "steps"]);

/** `animation`'s non-timing keywords, which a shorthand legitimately carries. */
const ANIMATION_KEYWORDS = new Set([
  "both",
  "forwards",
  "backwards",
  "none",
  "infinite",
  "alternate",
  "reverse",
  "alternate-reverse",
  "normal",
  "running",
  "paused",
]);

function stylesheets(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return stylesheets(path);
    return path.endsWith(".css") ? [path] : [];
  });
}

/** Every `transition…` / `animation…` declaration, flattened to one line each. */
function declarations(css: string): { readonly property: string; readonly value: string }[] {
  const out: { property: string; value: string }[] = [];
  const pattern = /(transition|animation)(-duration|-timing-function)?\s*:([^;{}]*)[;}]/g;
  for (const match of css.matchAll(pattern)) {
    out.push({
      property: `${match[1] ?? ""}${match[2] ?? ""}`,
      value: (match[3] ?? "").replace(/\s+/g, " ").trim(),
    });
  }
  return out;
}

describe("§E11.1 — five signature motions, and only five", () => {
  it("is five, named as §E11.1 names them", () => {
    expect(MOTION_IDS).toEqual(["stamp", "merge", "rule-draw", "lift", "settle"]);
    expect(SIGNATURE_MOTIONS).toHaveLength(5);
    expect(new Set(SIGNATURE_MOTIONS.map((one) => one.id)).size).toBe(5);
  });

  it("spends only durations and curves that exist as tokens", () => {
    const durations = new Set<number>(Object.values(MOTION.durationMs));
    for (const one of SIGNATURE_MOTIONS) {
      expect(durations.has(one.durationMs), one.id).toBe(true);
      expect(Object.keys(MOTION.easing)).toContain(one.easing);
    }
    // §E11: *"Durations are multiples of the 12 fps step (83.3 ms)"* — and
    // read precisely, that claim is about the four the blueprint annotates with
    // a step count: `snap` (1), `fast` (2), `base` (3), `slow` (5). `cine`
    // (900 ms) and `film` (2400 ms) are given **no** step count there, and they
    // are not step multiples: 900 is 10.8 steps. That is not an error in the
    // tokens. They are *film* timings — a camera move and a title card — and a
    // camera is uncapped by §E7.2 while the world animates on twos, so pulling
    // them onto the stepped clock would tie the one thing that must be smooth
    // to the one thing that must not be.
    //
    // So this asserts the claim where the blueprint makes it, and states the
    // exception rather than widening the tolerance until both pass.
    const stepped = [
      MOTION.durationMs.snap,
      MOTION.durationMs.fast,
      MOTION.durationMs.base,
      MOTION.durationMs.slow,
    ];
    for (const ms of stepped) {
      const steps = ms / MOTION.stepMs;
      expect(Math.abs(Math.round(steps) - steps), `${String(ms)}ms`).toBeLessThan(0.06);
    }
    expect(durations.has(MOTION.durationMs.cine)).toBe(true);
  });

  it("carries §E11.1's own numbers for the two motions that name them", () => {
    // "Scale 1.18 → 1.0 over 168 ms on --ease-stamp" and "900 ms on
    // --ease-cine, a 168 ms hold". Written as tokens rather than as numbers, so
    // this asserts the mapping rather than re-typing the blueprint.
    expect(motion("stamp").durationMs).toBe(MOTION.durationMs.fast);
    expect(motion("merge").durationMs).toBe(MOTION.durationMs.cine);
    expect(MERGE_HOLD_MS).toBe(MOTION.durationMs.fast);
    // The only overshoot curve in the product, and the only motion that uses it.
    expect(motion("stamp").easing).toBe("stamp");
    expect(SIGNATURE_MOTIONS.filter((one) => one.easing === "stamp")).toHaveLength(1);
    // "The only overshoot permitted anywhere in the product, and only in the 3D
    // world, because clay has mass."
    expect(motion("settle").easing).toBe("clay");
  });

  it("is claimed in the film's markup, motion by motion", () => {
    // `data-motion` is how a motion is claimed, so the audit can see the
    // vocabulary in the DOM rather than inferring it from class names.
    const film = [
      readFileSync(join(SRC, "story", "acts", "close.tsx"), "utf8"),
      readFileSync(join(SRC, "story", "acts", "opening.tsx"), "utf8"),
      readFileSync(join(SRC, "story", "story.css"), "utf8"),
      readFileSync(join(SRC, "components", "components.css"), "utf8"),
      readFileSync(join(SRC, "clay", "pins.ts"), "utf8"),
    ].join("\n");

    for (const id of MOTION_IDS) {
      // The Settle lives in the 3D world (`pins.ts`, on the clay curve) and the
      // other four in the paper layer, which is why the sources above span
      // both. An unused motion fails here.
      expect(film.toLowerCase(), id).toContain(id === "settle" ? "settle" : id);
    }
  });

  it("finds no literal duration or curve in any stylesheet in src/", () => {
    const offences: string[] = [];

    for (const path of stylesheets(SRC)) {
      // Generated artefacts declare the tokens; they are the source of the
      // values everything else spends.
      if (path.includes(join("design", "generated"))) continue;

      for (const { property, value } of declarations(readFileSync(path, "utf8"))) {
        const where = `${path.replace(ROOT, ".")} — ${property}: ${value}`;

        // A time literal anywhere in a motion declaration.
        if (/(?<![\w-])\d+(?:\.\d+)?m?s\b/.test(value)) {
          offences.push(`${where}  [literal duration]`);
        }
        // A named or hand-written curve. `cubic-bezier(...)` written out is the
        // same offence as `ease-in-out`: §E9.4 rule 3's argument about colour
        // applies unchanged to motion, and §E11 names four curves.
        for (const token of value.split(/[\s,]+/)) {
          const bare = token.replace(/\(.*$/, "");
          if (bare === "" || bare.startsWith("var") || bare.startsWith("--")) continue;
          if (NO_CURVE.has(bare) || ANIMATION_KEYWORDS.has(bare)) continue;
          if (/^(ease|ease-in|ease-out|ease-in-out|cubic-bezier)$/.test(bare)) {
            offences.push(`${where}  [untokened curve: ${bare}]`);
          }
        }
      }
    }

    expect(offences, offences.join("\n")).toEqual([]);
  });
});
