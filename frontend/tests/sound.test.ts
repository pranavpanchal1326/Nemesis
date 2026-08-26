import { describe, expect, it } from "vitest";

import { SOUND } from "../src/design/generated/tokens.ts";
import { REALTIME_SHAPED_EVENT_TYPES } from "../src/generated/enums.ts";
import {
  BEDS,
  BUSES,
  CUES,
  DEFECT_LOOPS,
  VOCABULARY,
  bedForSun,
  type Cue,
} from "../src/sound/cues.ts";
import { busGainFor } from "../src/sound/graph.ts";
import { EVENT_CUES, cueFor } from "../src/sound/live-sound.ts";
import { lengthOf, render } from "../src/sound/synth.ts";
import {
  loopForCategory,
  reconcileVoices,
  voicesFor,
  type AudibleEntity,
} from "../src/sound/world-sound.ts";

/**
 * §E12 and §E3.4, asserted — F16, M10.
 *
 * Sound is the hardest thing in this project to hold to a standard, because the
 * obvious way to check it is to listen and the obvious way to check it in CI is
 * not to. ADR-0050's synthesise-don't-record decision is what makes this file
 * possible: a cue is a pure function of numbers, so *"three soft taps
 * converging into one low thump"* is a claim about where four transients land,
 * and that is a number a test can read.
 *
 * Four things are asserted here, one per part of the gate:
 *
 * · **§E3.4** — the vocabulary is a set of distinct meanings, and every meaning
 *   is used once. The `single-meaning` guards in `scripts/check-guards.ts` do
 *   the source-level half; this does the table-level half.
 * · **The synthesiser is deterministic**, which is what makes a cue cacheable
 *   and this file meaningful.
 * · **`prefers-reduced-motion`** silences the continuous buses and keeps the
 *   informational ones.
 * · **Positional foley is an affordance**: nearer is louder, and the direction
 *   is real.
 */

// --------------------------------------------------------------------------
// §E3.4 — the vocabulary audit
// --------------------------------------------------------------------------

describe("§E3.4 — each sound carries exactly one meaning", () => {
  it("gives every cue in the product a distinct meaning", () => {
    const meanings = Object.values(VOCABULARY).map((cue: Cue) => cue.means);
    expect(new Set(meanings).size, meanings.join(" · ")).toBe(meanings.length);
  });

  it("states a meaning for every cue, in words rather than a slug", () => {
    for (const [name, cue] of Object.entries(VOCABULARY)) {
      expect(cue.means.length, name).toBeGreaterThan(12);
      expect(cue.means, name).toMatch(/\s/);
    }
  });

  it("puts every cue on one of §E12's four buses", () => {
    for (const [name, cue] of Object.entries(VOCABULARY)) {
      expect(BUSES as readonly string[], name).toContain(cue.bus);
    }
  });

  it("ships exactly the six interaction cues §E12 lists, plus the merge and the note", () => {
    // §E12 names its foley: "stamp thud, paper slide on panel open, pin push on
    // select, page turn on route change, shutter on capture, and a roller pass
    // on print transitions", then the merge cue and the struck note. Eight, and
    // a ninth would need a sentence in the blueprint before it needs code.
    expect(Object.keys(CUES)).toEqual([
      "stamp",
      "paperSlide",
      "pinPush",
      "pageTurn",
      "shutter",
      "rollerPass",
      "merge",
      "alarm",
    ]);
  });

  it("keeps the alarming sound alone on its own bus", () => {
    // §E12: the struck note is "the only alarming sound in the product". A
    // second cue on the alert bus would be a second alarming sound by
    // construction.
    const alerts = Object.entries(VOCABULARY).filter(([, cue]) => cue.bus === "alert");
    expect(alerts.map(([name]) => name)).toEqual(["alarm"]);
  });
});

describe("§E12 — the two cues the system plays are bound to real events", () => {
  it("binds the merge and the fail-safe, and nothing else", () => {
    expect(Object.keys(EVENT_CUES)).toEqual(["cluster_match_found", "safety_trigger_fired"]);
  });

  it("binds only to events this system actually publishes", () => {
    for (const eventType of Object.keys(EVENT_CUES)) {
      expect(REALTIME_SHAPED_EVENT_TYPES as readonly string[], eventType).toContain(eventType);
    }
  });

  it("says nothing for the events that mean nothing to a listener", () => {
    expect(cueFor("severity_scored")).toBeNull();
    expect(cueFor("citizen_confirmed")).toBeNull();
  });
});

// --------------------------------------------------------------------------
// The synthesiser (ADR-0050)
// --------------------------------------------------------------------------

describe("ADR-0050 — the library is computed, and computes the same thing twice", () => {
  it("renders bit-identical samples for the same cue", () => {
    const first = render(CUES.stamp.recipe);
    const second = render(CUES.stamp.recipe);
    expect(Array.from(first)).toEqual(Array.from(second));
  });

  it("renders every cue in the product without producing silence", () => {
    for (const [name, cue] of Object.entries(VOCABULARY)) {
      const samples = render(cue.recipe);
      expect(samples.length, name).toBeGreaterThan(0);
      let peak = 0;
      for (const value of samples) peak = Math.max(peak, Math.abs(value));
      expect(peak, `${name} rendered silence`).toBeGreaterThan(0.1);
      // Nothing may clip: a cue that leaves the buffer above 1 is a cue that
      // distorts on a device with no headroom, and every recipe is normalised.
      expect(peak, `${name} clips`).toBeLessThanOrEqual(1.0001);
    }
  });

  it("lands the merge's four transients where §E12 spaces them", () => {
    // "Three soft taps converging into one low thump." The taps are one gap
    // apart and the thump follows the third — asserted as *when the energy
    // arrives*, which is the only part of "converging" a number can carry.
    const samples = render(CUES.merge.recipe);
    const at = (ms: number): number => Math.round((ms / 1000) * SOUND.sampleRate);
    const energyNear = (ms: number): number => {
      const centre = at(ms);
      let sum = 0;
      for (let i = centre; i < Math.min(samples.length, centre + at(20)); i += 1) {
        sum += Math.abs(samples[i] ?? 0);
      }
      return sum;
    };

    for (const tap of [0, SOUND.merge.gapMs, SOUND.merge.gapMs * 2, SOUND.merge.gapMs * 3]) {
      expect(energyNear(tap), `no transient at ${String(tap)} ms`).toBeGreaterThan(0.5);
    }
    // And silence in the gaps, which is what makes them taps rather than a roll.
    expect(energyNear(SOUND.merge.gapMs - 30)).toBeLessThan(energyNear(SOUND.merge.gapMs));
  });

  it("rings the struck note for as long as §E12's gravity needs", () => {
    const samples = render(CUES.alarm.recipe);
    expect(samples.length).toBe(Math.round((SOUND.note.decayMs / 1000) * SOUND.sampleRate));
    // Still audible three-quarters of the way through: "grave, not panicked"
    // is a decay, and a note that had died by then would be a click.
    const late = samples[Math.floor(samples.length * 0.75)] ?? 0;
    expect(Math.abs(late)).toBeGreaterThan(0);
  });

  it("returns a bed that is all loop, with no silent tail", () => {
    // **This assertion found a defect.** The seam is cross-faded into the head
    // so the loop does not click, and the first implementation then left the
    // faded tail in the buffer as silence — so a looping bed played eight
    // seconds of city and four hundred milliseconds of nothing, once a lap,
    // forever. A loop buffer must be entirely loop.
    const rendered = render(BEDS.midday.recipe);
    expect(rendered.length).toBe(lengthOf(BEDS.midday.recipe));
    expect(rendered.length).toBeLessThan(Math.round(BEDS.midday.recipe.seconds * SOUND.sampleRate));

    let tail = 0;
    for (let i = rendered.length - 200; i < rendered.length; i += 1) {
      tail += Math.abs(rendered[i] ?? 0);
    }
    expect(tail, "the end of the loop is silent").toBeGreaterThan(0.5);
  });
});

// --------------------------------------------------------------------------
// §E12 — the buses and the preference
// --------------------------------------------------------------------------

describe("§E12 — every bus respects prefers-reduced-motion", () => {
  it("silences the continuous buses under the preference", () => {
    expect(busGainFor("ambient", true)).toBe(0);
    expect(busGainFor("positional", true)).toBe(0);
  });

  it("keeps the informational cues, because they carry meaning rather than load", () => {
    expect(busGainFor("foley", true)).toBeGreaterThan(0);
    expect(busGainFor("alert", true)).toBeGreaterThan(0);
  });

  it("runs every bus at its token gain when no preference is set", () => {
    for (const bus of BUSES) {
      expect(busGainFor(bus, false), bus).toBe(SOUND.gain[bus]);
    }
  });
});

describe("§E7.4 — the bed is the sun's answer, not the browser's clock", () => {
  it("takes midday from the same altitude the key light ramps to", () => {
    expect(bedForSun(60, 180)).toBe("midday");
    expect(bedForSun(20, 90)).toBe("midday");
  });

  it("splits morning from dusk on which half of the sky the sun is in", () => {
    expect(bedForSun(5, 80)).toBe("morning");
    expect(bedForSun(5, 280)).toBe("dusk");
  });

  it("answers for a sun below the horizon rather than failing", () => {
    // Night is a real state and the scene renders it (`keyIntensity`). A bed
    // chooser that threw at 03:00 would take the whole console down overnight.
    expect(bedForSun(-30, 20)).toBe("morning");
    expect(bedForSun(-30, 200)).toBe("dusk");
    expect(bedForSun(5, 400)).toBe("morning");
  });
});

// --------------------------------------------------------------------------
// §E12 — positional foley is an affordance
// --------------------------------------------------------------------------

const HERE = { x: 0, z: 0 };

function defect(id: string, x: number, z: number, category: string | null): AudibleEntity {
  return { id, x, z, category };
}

describe("§E12 — an operator can hear where the problems are", () => {
  it("matches the tenant's own words rather than a closed list of defect types", () => {
    expect(loopForCategory("Waterlogging on arterial roads")).toBe("water");
    expect(loopForCategory("Street light not working")).toBe("electrical");
    expect(loopForCategory("Garbage not collected")).toBe("waste");
  });

  it("is silent for a category nobody matched, rather than defaulting", () => {
    // §6 Principle #9: a sound that fires for everything is decoration.
    expect(loopForCategory("Stray cattle")).toBeNull();
    expect(loopForCategory(null)).toBeNull();
  });

  it("makes a nearer defect louder", () => {
    const [near, far] = [
      defect("near", 10, 0, "waterlogging"),
      defect("far", 200, 0, "waterlogging"),
    ];
    const voices = voicesFor([near, far], HERE);
    const gains = new Map(voices.map((voice) => [voice.id, voice.gain]));
    expect(gains.get("near")).toBeGreaterThan(gains.get("far") ?? 1);
  });

  it("pans by real direction, so 'where' is true rather than decorative", () => {
    const voices = voicesFor(
      [defect("east", 100, 0, "waterlogging"), defect("west", -100, 0, "waterlogging")],
      HERE,
    );
    const pans = new Map(voices.map((voice) => [voice.id, voice.pan]));
    expect(pans.get("east")).toBeGreaterThan(0);
    expect(pans.get("west")).toBeLessThan(0);
  });

  it("drops anything beyond the audible radius", () => {
    const beyond = SOUND.positional.maxDistanceMetres + 10;
    expect(voicesFor([defect("far", beyond, 0, "waterlogging")], HERE)).toHaveLength(0);
  });

  it("caps the voices and keeps the nearest, not an arbitrary dozen", () => {
    const many = Array.from({ length: SOUND.positional.maxVoices + 8 }, (_, i) =>
      defect(`d${String(i)}`, (i + 1) * 5, 0, "waterlogging"),
    );
    const voices = voicesFor(many, HERE);
    expect(voices).toHaveLength(SOUND.positional.maxVoices);
    expect(voices[0]?.id).toBe("d0");
  });

  it("does not restart a voice that is already playing", () => {
    // A loop that restarted every time the camera moved would stutter, which is
    // the opposite of "you can hear where it is".
    let stops = 0;
    const playing = new Map<string, () => void>([
      [
        "keep",
        () => {
          stops += 1;
        },
      ],
      [
        "gone",
        () => {
          stops += 1;
        },
      ],
    ]);
    const next = reconcileVoices(playing, [{ id: "keep", loop: "water", pan: 0, gain: 1 }]);
    expect(stops).toBe(1);
    expect(next.has("keep")).toBe(true);
    expect(next.has("gone")).toBe(false);
  });

  it("declares a loop for every defect family it can name", () => {
    for (const family of ["water", "electrical", "waste"] as const) {
      expect(DEFECT_LOOPS[family].bus).toBe("positional");
    }
  });
});
