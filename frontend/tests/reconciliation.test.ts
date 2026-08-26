import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { SURFACE_CLAIMS } from "@/public/generated/honesty";
import { SCREENS } from "@/console/screens";

/**
 * M12.1 and M12.5 — §E28 verified line by line, and executed thereafter.
 *
 * > Every claim on the frontend rows of §44 traces to a passing test or a
 * > shipped artefact. **No REAL row is backed by a fixture.**
 * >   — the F18 gate, `docs/FRONTEND-PHASE-PLAN.md`
 *
 * `honesty:check` already fails CI when the published table drifts from the
 * blueprints, and `honesty.test.ts` already asserts the table is well-formed as
 * data. Neither can ask the only question that matters at F18: **is the row
 * true?** A status label is a claim about a codebase, and the codebase is right
 * here.
 *
 * So every §E28 row carries an entry below naming what backs it, and the entry
 * is checked rather than read:
 *
 * · every path named must exist and be non-empty — a citation to a deleted file
 *   is worse than no citation;
 * · a row whose **component** reads REAL must name a test or a shipped
 *   artefact, which is the gate's own wording;
 * · a **finished** row — REAL component over REAL data — must name no source
 *   file that draws a fixture. Not "should not": the check greps each named
 *   file for `<NotWired>`, `<FixtureNotice>` and `<ContractGap>`, and for the
 *   roadmap directory those three live in;
 * · every console screen the registry marks `roadmap` must be claimed by a row
 *   whose **data** is not REAL, so a screen drawn from fixture values cannot be
 *   absent from the honesty table or sitting on a finished row.
 *
 * **Writing this found three wrong rows**, and none of them was found by
 * reading the table:
 *
 * 1. *Golden images, Storybook diffs, Lighthouse, WCAG audit, usability
 *    session* read **ROADMAP** as one row. F1 shipped the first three and F3
 *    completed the third; the two that need a person had not happened. One
 *    label over five clauses meant the shipped three were understated for five
 *    milestones and the unbooked two were invisible. Split.
 * 2. §44's *Public read-only transparency API* read **ROADMAP (schema
 *    described)** — and M6 shipped every endpoint §26.4 describes. Understating
 *    is still drift, and it is the direction nobody audits for.
 * 3. §44's *Underreporting-zone equity flag* read **REAL**. It is rendered in
 *    exactly one place in this product: `console/roadmap/AreaView.tsx`, behind
 *    the §E24 chip, beside a `<ContractGap>` naming the endpoint that does not
 *    exist. **A REAL row backed by a fixture** — the literal thing M12.5 is a
 *    gate against, and it had been sitting in the master table since v2.0.
 *
 * The ledger below is the reconciliation. It is long on purpose: a row nobody
 * had to write evidence for is a row nobody checked.
 */

const WEB = join(fileURLToPath(new URL(".", import.meta.url)), "..");

/** What backs one §E28 row. Paths are relative to `frontend/`. */
interface Evidence {
  /** Test files that fail if the claim stops being true. */
  readonly tests: readonly string[];
  /** The implementation. Checked for fixture primitives on finished rows. */
  readonly source: readonly string[];
  /** A shipped, inspectable artefact where a test is not the right proof — a
   *  generated file, a committed report, a config that gates CI. */
  readonly artefacts?: readonly string[];
  /** Console screen ids from `SCREENS` that this row is the honesty statement
   *  for. Checked against the registry's own `wiring`. */
  readonly screens?: readonly string[];
}

/**
 * Every §E28 row, and what stands behind it.
 *
 * A ROADMAP row may legitimately name nothing built — but it still names the
 * document or report that records *why*, so that "not built" is a position
 * somebody took rather than a silence.
 */
const EVIDENCE: Readonly<Record<string, Evidence>> = {
  "Design system, tokens, press, type stack": {
    tests: ["tests/contrast.test.ts", "tests/press-parity.test.ts", "tests/type.spec.ts"],
    source: ["src/design/tokens.json", "src/press/Press.tsx", "src/press/press-tsl.ts"],
    artefacts: ["src/design/generated/tokens.css", "src/design/generated/tokens.ts"],
  },
  "§E26 component contracts (badge, trail, before/after, flagged, suppression, receipt, ledger, banner, stamp, clay scene)":
    {
      tests: ["tests/contracts.test.ts", "tests/contracts.spec.ts"],
      source: [
        "src/components/SeverityBadge.tsx",
        "src/components/EvidenceTrail.tsx",
        "src/components/BeforeAfter.tsx",
        "src/components/FlaggedNotice.tsx",
        "src/components/SuppressionNotice.tsx",
        "src/components/Receipt.tsx",
        "src/components/ContractorLedger.tsx",
        "src/components/DegradedBanner.tsx",
        "src/components/Stamp.tsx",
        "src/clay/ClayScene.tsx",
      ],
    },
  "Generated client, BFF seam, WebSocket store, reconciliation rule": {
    tests: ["tests/realtime.test.ts", "tests/bridge.test.ts", "tests/types.test.ts"],
    source: [
      "src/server/upstream.ts",
      "src/lib/realtime/socket.ts",
      "src/lib/realtime/reconcile.ts",
    ],
    artefacts: ["src/generated/api.ts", "src/generated/enums.ts"],
  },
  "Report capture → submit → receipt": {
    tests: ["tests/citizen.spec.ts", "tests/mutations.test.ts"],
    source: [
      "src/citizen/ReportFlow.tsx",
      "src/citizen/Viewfinder.tsx",
      "src/components/Receipt.tsx",
    ],
  },
  "Pipeline theatre — six gates, not five": {
    tests: ["tests/citizen.spec.ts"],
    source: ["src/citizen/PipelineTheatre.tsx", "src/citizen/gates.ts"],
  },
  "The third outcome (pending_classification, §24.2)": {
    tests: ["tests/citizen.spec.ts"],
    source: ["src/citizen/gates.ts"],
  },
  "Tracking ledger from the event log": {
    tests: ["tests/citizen.spec.ts", "tests/console-real.spec.ts"],
    source: ["src/components/EvidenceTrail.tsx", "src/citizen/TrackScreen.tsx"],
  },
  'Dedup payoff — "you\'re the 4th person"': {
    tests: ["tests/citizen.spec.ts"],
    source: ["src/citizen/DedupPayoff.tsx"],
  },
  "Severity breakdown panel": {
    // Component REAL over SIMULATED data: the rubric opens, and the fields it
    // would print return null. `<SeverityWhy>` carries the chip for exactly
    // that reason, which is why this row is not finished and must not be.
    tests: ["tests/citizen.spec.ts"],
    source: ["src/citizen/SeverityWhy.tsx", "src/lib/severity.ts"],
  },
  "Cluster-merge hero, live": {
    tests: ["tests/story.spec.ts", "tests/story-live.test.ts"],
    source: ["src/story/live-story.ts", "src/clay/live.ts", "src/clay/thumbprint.ts"],
    artefacts: ["../docs/reports/story-merge-gate.md"],
  },
  "Live map, instanced pins": {
    tests: ["tests/clay.spec.ts", "tests/clay-world.test.ts", "tests/clay-live.test.ts"],
    source: [
      "src/clay/pins.ts",
      "src/clay/scene.ts",
      "src/clay/renderer.ts",
      "src/clay/PeerList.tsx",
    ],
    artefacts: ["../docs/reports/clay-frame-rate.md"],
  },
  "Temporal replay — endpoint and UI both": {
    // Nothing built, and nothing pretending to be. The row's own note records
    // that an earlier draft wrongly listed the backend as shipped, which is the
    // correction §E28 refuses to erase.
    tests: [],
    source: [],
    artefacts: ["../NEMESIS-Frontend-Blueprint.md"],
  },
  '"Your Ward\'s Month" film': {
    tests: [],
    source: [],
    artefacts: ["../NEMESIS-Frontend-Blueprint.md"],
  },
  "Review queue": {
    tests: ["tests/console-real.spec.ts", "tests/console-queue.test.ts"],
    source: ["src/console/review/ReviewQueue.tsx", "src/console/review/queue.ts"],
    screens: ["review"],
  },
  "Policy studio + simulation": {
    tests: ["tests/console-real.spec.ts"],
    source: [
      "src/console/policy/PolicyStudio.tsx",
      "src/console/policy/ActivateControl.tsx",
      "src/console/policy/diff.ts",
    ],
    screens: ["policy"],
  },
  "Control-plane admin (taxonomy, zones, departments, calendars, locales)": {
    tests: ["tests/console-real.spec.ts"],
    source: [
      "src/console/control/ControlPlane.tsx",
      "src/console/control/TenantForm.tsx",
      "src/console/control/PublicationControl.tsx",
    ],
    screens: ["control"],
  },
  "Developer portal (keys, webhooks, usage, versions)": {
    tests: ["tests/console-real.spec.ts"],
    source: ["src/console/control/DeveloperPortal.tsx"],
    screens: ["developers"],
  },
  "Public place pages (zone / ward)": {
    tests: ["tests/public.spec.ts", "tests/public-figures.test.ts"],
    source: ["src/app/(public)/[tenant]/ward/[zoneCode]/page.tsx", "src/public/ZonePanel.tsx"],
  },
  "Public contractor and budget pages": {
    tests: ["tests/public.spec.ts"],
    source: [
      "src/app/(public)/[tenant]/contractor/[contractorId]/page.tsx",
      "src/app/(public)/[tenant]/budget/[zoneCode]/page.tsx",
    ],
  },
  "Suppression rendered rather than blanked": {
    tests: ["tests/public-figures.test.ts", "tests/public.spec.ts"],
    source: [
      "src/public/figures.ts",
      "src/public/Figure.tsx",
      "src/components/SuppressionNotice.tsx",
    ],
  },
  "Share cards (satori + resvg)": {
    tests: ["tests/public.spec.ts"],
    source: ["src/public/share-card.tsx"],
  },
  "Role-based console shells": {
    tests: ["tests/console-roadmap.spec.ts", "tests/console-screens.test.ts"],
    source: ["src/console/roadmap/Roles.tsx"],
    screens: ["roles"],
  },
  "Command view SLA countdowns": {
    // ROADMAP over a REAL screen, and deliberately so: the command view carries
    // real queue figures and renders the breach line's chip and the sentence it
    // will say, rather than a number it cannot know.
    tests: ["tests/console.spec.ts"],
    source: ["src/console/CommandView.tsx"],
  },
  "Area view + underreporting signal": {
    tests: ["tests/console-roadmap.spec.ts"],
    source: ["src/console/roadmap/AreaView.tsx"],
    screens: ["area"],
  },
  "Work order, assignment, contractor picker, budget entry": {
    tests: ["tests/console-roadmap.spec.ts"],
    source: ["src/console/roadmap/WorkOrder.tsx"],
    screens: ["work"],
  },
  "Milestone gate strip": {
    tests: ["tests/console-roadmap.spec.ts"],
    source: ["src/console/roadmap/WorkOrder.tsx"],
  },
  "Closure gates + SSIM display": {
    tests: ["tests/console-roadmap.spec.ts"],
    source: ["src/console/roadmap/Closure.tsx", "src/components/BeforeAfter.tsx"],
    screens: ["closure"],
  },
  "Money view": {
    tests: ["tests/console-roadmap.spec.ts"],
    source: ["src/console/roadmap/Money.tsx"],
    screens: ["money"],
  },
  "Integrity room, case file, blacklist flow": {
    tests: ["tests/console-roadmap.spec.ts"],
    source: ["src/console/roadmap/Integrity.tsx"],
    screens: ["integrity"],
  },
  "Contractor portal + appeals": {
    tests: [],
    source: [],
    artefacts: ["../NEMESIS-Frontend-Blueprint.md"],
  },
  "Report builder + verifiable PDF": {
    tests: ["tests/console-roadmap.spec.ts"],
    source: ["src/console/roadmap/ReportBuilder.tsx"],
    screens: ["reports"],
  },
  "Honesty table published as data (§44 + §E28)": {
    tests: ["tests/honesty.test.ts", "tests/reconciliation.test.ts"],
    source: ["src/public/HonestyTable.tsx", "scripts/generate-honesty.ts"],
    artefacts: ["src/public/generated/honesty.ts"],
  },
  "RTI draft generation": {
    tests: [],
    source: [],
    artefacts: ["../NEMESIS-Blueprint-v2.md"],
  },
  "Accident-prone & traffic overlays": {
    tests: [],
    source: [],
    artefacts: ["../NEMESIS-Frontend-Blueprint.md"],
  },
  "PWA, offline queue, outdoor mode": {
    tests: ["tests/field.spec.ts", "tests/offline.test.ts", "tests/contrast.test.ts"],
    source: [
      "src/lib/offline/queue.ts",
      "src/lib/offline/db.ts",
      "src/lib/media/compress.ts",
      "src/lib/media/exif.ts",
      "src/field/FieldScreen.tsx",
      "src/field/outdoor.ts",
    ],
    artefacts: ["public/sw.js", "src/app/manifest.ts"],
  },
  "Sound design": {
    tests: ["tests/sound.test.ts"],
    source: [
      "src/sound/graph.ts",
      "src/sound/synth.ts",
      "src/sound/cues.ts",
      "src/sound/live-sound.ts",
    ],
  },
  "Positional foley — hear where the problems are": {
    // Built, asserted as a pure function, and wired to nothing — because the
    // only positioned data this frontend receives is wards. The report is the
    // evidence; the source is named so the claim "the mechanism exists" is
    // checkable, and the row is ROADMAP so it is exempt from the fixture rule.
    tests: ["tests/sound.test.ts"],
    source: ["src/sound/world-sound.ts"],
    artefacts: ["../docs/reports/positional-foley-gap.md"],
  },
  "The character layer — the four figures of §E8.2": {
    tests: ["tests/ink-machine.test.ts", "tests/ink-figures.test.ts", "tests/ink.spec.ts"],
    source: ["src/ink/machine.ts", "src/ink/figures.ts", "src/ink/draw.ts", "src/ink/live-ink.ts"],
    artefacts: ["../docs/reports/character-relief-gate.md"],
  },
  "Tiers S / A / B / C / D fallback ladder": {
    tests: ["tests/ladder.spec.ts", "tests/clay-ladder.test.ts"],
    source: ["src/clay/tier.ts", "src/clay/quality.ts", "src/lib/device.ts"],
  },
  "Golden images, Storybook diffs, Lighthouse budgets": {
    tests: ["tests/golden.spec.ts", "tests/origin.spec.ts"],
    source: ["scripts/storybook-diff.ts"],
    artefacts: ["lighthouserc.json", "playwright.config.ts"],
  },
  "WCAG 2.2 AA verified by a person; measured task-success from a usability session": {
    // A15 and A16. The automated half is named because it is real and because
    // naming it is what stops the row reading as *nothing has been done*; the
    // reports are named because the half that is a person has not happened, and
    // an unbooked session with a written protocol is a different state from an
    // unbooked session with nothing.
    tests: ["tests/console.spec.ts", "tests/public.spec.ts", "tests/story.spec.ts"],
    source: [],
    artefacts: ["../docs/reports/wcag-audit-gap.md", "../docs/reports/usability-session-gap.md"],
  },
};

/** `<NotWired>`, `<FixtureNotice>` and `<ContractGap>` — the three ways this
 *  codebase says *these values are invented*. A finished row may name none. */
const FIXTURE_MARKERS = ["NotWired", "FixtureNotice", "ContractGap"];
const FIXTURE_DIRECTORY = "console/roadmap/";

function evidenceFor(capability: string): Evidence | undefined {
  return EVIDENCE[capability];
}

function allPaths(evidence: Evidence): readonly string[] {
  return [...evidence.tests, ...evidence.source, ...(evidence.artefacts ?? [])];
}

describe("every §E28 row is accounted for", () => {
  it("has an evidence entry, and no entry is orphaned", () => {
    const capabilities = new Set(SURFACE_CLAIMS.map((claim) => claim.capability));

    const unclaimed = SURFACE_CLAIMS.filter((claim) => evidenceFor(claim.capability) === undefined);
    expect(
      unclaimed.map((claim) => claim.capability),
      "§E28 rows with nothing named behind them — a row nobody had to write evidence for is a row nobody checked",
    ).toEqual([]);

    const orphaned = Object.keys(EVIDENCE).filter((key) => !capabilities.has(key));
    expect(
      orphaned,
      "evidence for rows that no longer exist on §E28 — the ledger drifted from the table",
    ).toEqual([]);
  });
});

describe("evidence is a citation, not a sentence", () => {
  it.each(Object.entries(EVIDENCE))("%s names only files that exist", (capability, evidence) => {
    for (const path of allPaths(evidence)) {
      const full = join(WEB, path);
      expect(existsSync(full), `${capability}: ${path} does not exist`).toBe(true);
      expect(statSync(full).size, `${capability}: ${path} is empty`).toBeGreaterThan(0);
    }
  });
});

describe("M12.1 — a REAL claim traces to a passing test or a shipped artefact", () => {
  it.each(SURFACE_CLAIMS.filter((claim) => claim.component === "REAL").map((c) => [c.capability]))(
    "%s",
    (capability) => {
      const evidence = evidenceFor(capability);
      expect(evidence).toBeDefined();
      const proof = (evidence?.tests.length ?? 0) + (evidence?.artefacts?.length ?? 0);
      expect(
        proof,
        `${capability} reads REAL and names neither a test nor an artefact. ` +
          "That is the F18 gate's own wording, and a REAL row that cannot be traced is the " +
          "claim §E28 says is worse than no table at all.",
      ).toBeGreaterThan(0);
    },
  );
});

describe("M12.5 — no REAL row is backed by a fixture", () => {
  const finished = SURFACE_CLAIMS.filter(
    (claim) => claim.component === "REAL" && claim.data === "REAL",
  );

  it("there are finished rows to check, so the sweep is not vacuous", () => {
    expect(finished.length).toBeGreaterThan(10);
  });

  it.each(finished.map((claim) => [claim.capability]))("%s draws no fixture", (capability) => {
    const evidence = evidenceFor(capability);
    expect(evidence).toBeDefined();

    for (const path of evidence?.source ?? []) {
      expect(
        path.includes(FIXTURE_DIRECTORY),
        `${capability} is REAL over REAL and is implemented in ${path}, which is the ` +
          "roadmap directory. A finished row cannot live where the fixtures live.",
      ).toBe(false);

      const text = readFileSync(join(WEB, path), "utf8");
      for (const marker of FIXTURE_MARKERS) {
        expect(
          new RegExp(`<${marker}\\b`).test(text),
          `${capability} is REAL over REAL and ${path} renders <${marker}>. ` +
            "The chip is this product saying the values are invented; a row that says " +
            "REAL over it is the exact failure M12.5 is a gate against.",
        ).toBe(false);
      }
    }
  });
});

describe("the console registry and the honesty table agree", () => {
  const byId = new Map(SCREENS.map((screen) => [screen.id, screen]));

  it("every screen named on a row exists in the registry", () => {
    for (const [capability, evidence] of Object.entries(EVIDENCE)) {
      for (const id of evidence.screens ?? []) {
        expect(
          byId.has(id),
          `${capability} claims console screen '${id}', which does not exist`,
        ).toBe(true);
      }
    }
  });

  it("a screen's wiring and its row's data column say the same thing", () => {
    /**
     * The biconditional, and it is the point of this file. `wiring` is a fact
     * about the backend that `console-screens.test.ts` already enforces against
     * `devOnly()`; `data` is the same fact written in the honesty table. They
     * are maintained by different people at different times, which is precisely
     * why they are asserted equal here rather than assumed.
     */
    for (const claim of SURFACE_CLAIMS) {
      const evidence = evidenceFor(claim.capability);
      for (const id of evidence?.screens ?? []) {
        const screen = byId.get(id);
        expect(screen).toBeDefined();
        const realScreen = screen?.wiring.kind === "real";
        const realData = claim.data === "REAL";
        expect(
          realScreen,
          `console screen '${id}' is wired '${screen?.wiring.kind ?? "?"}' and §E28 says its ` +
            `data is ${String(claim.data)} — one of the two is lying about the same backend`,
        ).toBe(realData);
      }
    }
  });

  it("every fixture screen is claimed by a row that does not call it REAL", () => {
    // The direction that matters: a screen drawn from invented values must
    // appear in the honesty table, and must appear on a row that says so. A
    // fixture screen nobody wrote a row for is a screen the table does not know
    // exists.
    const claimed = new Map<string, string>();
    for (const claim of SURFACE_CLAIMS) {
      for (const id of evidenceFor(claim.capability)?.screens ?? []) {
        claimed.set(id, String(claim.data));
      }
    }

    for (const screen of SCREENS) {
      if (screen.wiring.kind !== "roadmap") continue;
      expect(claimed.has(screen.id), `fixture screen '${screen.id}' is on no §E28 row`).toBe(true);
      expect(
        claimed.get(screen.id),
        `fixture screen '${screen.id}' sits on a REAL data row`,
      ).not.toBe("REAL");
    }
  });
});
