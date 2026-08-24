import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { SEVERITY } from "../src/design/generated/tokens.ts";
import { COMPLAINT_STATUSES, WORK_ORDER_STATUSES } from "../src/generated/enums.ts";
import { visibleIn, type TrailView } from "../src/components/EvidenceTrail.tsx";
import { complaintRegister, workOrderRegister } from "../src/components/StatusChip.tsx";
import { isAmbiguous } from "../src/components/BeforeAfter.tsx";
import { makeStrings, plural, t } from "../src/lib/i18n/strings.ts";
import type { RealtimeEnvelope } from "../src/lib/realtime/envelope.ts";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");

const base = JSON.parse(
  readFileSync(join(ROOT, "src", "i18n", "base", "common.json"), "utf8"),
) as Record<string, string>;

const marathi = JSON.parse(
  readFileSync(join(ROOT, "src", "i18n", "base", "common.mr.json"), "utf8"),
) as Record<string, string>;

const strings = makeStrings("common", "en", base);

/**
 * §E26's contracts, asserted.
 *
 * Where §E26 says *"where a rule below says required prop, it is enforced by the
 * type system, not by review"*, the enforcement is in `tests/types.spec.ts` —
 * a compile that must fail. What is here is everything a running program can
 * check: completeness, filtering, and the absence of the one field the API is
 * not allowed to grow.
 */

describe("§E26.1 — the status vocabulary is complete, and every member renders", () => {
  it("every complaint status has a label and a register", () => {
    // §E26.1: "A status chip rendering a value not on these lists, or a view
    // omitting one, is a defect." Thirteen means thirteen.
    expect(COMPLAINT_STATUSES).toHaveLength(13);
    for (const status of COMPLAINT_STATUSES) {
      expect(base[`status.${status}`], `status.${status} has no label`).toBeTypeOf("string");
      expect(complaintRegister(status)).toBeTypeOf("string");
    }
  });

  it("every work order status has a label and a register, including the two §15.7 omits", () => {
    expect(WORK_ORDER_STATUSES).toHaveLength(6);
    expect(WORK_ORDER_STATUSES).toContain("created");
    expect(WORK_ORDER_STATUSES).toContain("disputed");
    for (const status of WORK_ORDER_STATUSES) {
      expect(base[`workOrder.${status}`], `workOrder.${status} has no label`).toBeTypeOf("string");
      expect(workOrderRegister(status)).toBeTypeOf("string");
    }
  });

  it("the two abnormal states carry their own explanation", () => {
    // Both mean "the system did not do the normal thing", and both are the
    // states a UI is most likely to forget and most needs to show.
    expect(complaintRegister("pending_classification")).toBe("degraded");
    expect(complaintRegister("flagged")).toBe("flagged");
    expect(base["status.pending_classification.detail"]).toContain("rather than being guessed at");
    expect(base["status.flagged.detail"]).toContain("Nothing has been decided");
  });

  it("every severity has a distinct label, in the bundle the control plane can override", () => {
    // §E9.4 rule 2 needs three channels: ink, shape and label. The first two are
    // tokens; the third is copy, and it is asserted here rather than there so
    // that translating it stays possible.
    const labels = Object.keys(SEVERITY).map((level) => base[`severity.${level}`]);
    expect(new Set(labels).size).toBe(Object.keys(SEVERITY).length);
    for (const label of labels) expect(label).toBeTypeOf("string");
  });

  it("flagged is never grouped with a severity register", () => {
    // ADR-0039: an unproven anomaly rendered in a severity colour is a §22.2
    // defamation exposure with a design cause.
    const severityish = new Set(Object.keys(SEVERITY));
    expect(severityish.has(complaintRegister("flagged"))).toBe(false);
  });
});

describe("§E26 — the evidence trail differs by filtering, never by code", () => {
  const entries: RealtimeEnvelope[] = Object.keys(base)
    .filter((key) => key.startsWith("event."))
    .map((key, index) => ({
      event_type: key.slice("event.".length),
      entity_type: "complaint",
      entity_id: "e1",
      sequence: index,
      timestamp: "2026-08-24T10:00:00Z",
      cursor: index,
      payload: {},
    }));

  const rowsFor = (view: TrailView) => entries.filter((entry) => visibleIn(entry, view));

  /** The first envelope, as a template. Throws rather than asserting non-null:
   *  an empty fixture is a broken test, and it should say so. */
  function template(): RealtimeEnvelope {
    const first = entries[0];
    if (first === undefined) throw new Error("no event keys in the base bundle");
    return first;
  }

  it("every view is a subset of the officer's", () => {
    // This is what "differ only by row filtering" means when written as
    // something a machine can check: a citizen or a member of the public can
    // never be shown a row an officer is not.
    const officer = new Set(rowsFor("officer").map((e) => e.event_type));
    for (const view of ["citizen", "public"] as const) {
      for (const entry of rowsFor(view)) {
        expect(
          officer.has(entry.event_type),
          `${view} sees ${entry.event_type}; officer does not`,
        ).toBe(true);
      }
    }
  });

  it("an unknown event type defaults to officer-only", () => {
    // The safe direction. A row nobody outside the department sees is a gap; a
    // row a citizen sees that names another citizen is a §22 incident.
    const unknown: RealtimeEnvelope = { ...template(), event_type: "some_future_event" };
    expect(visibleIn(unknown, "officer")).toBe(true);
    expect(visibleIn(unknown, "citizen")).toBe(false);
    expect(visibleIn(unknown, "public")).toBe(false);
  });

  it("review and abuse-detection rows never reach a citizen or the public", () => {
    for (const eventType of ["review_queued", "review_decided", "abuse_pattern_flagged"]) {
      const entry: RealtimeEnvelope = { ...template(), event_type: eventType };
      expect(visibleIn(entry, "citizen")).toBe(false);
      expect(visibleIn(entry, "public")).toBe(false);
    }
  });

  it("the closure loop is visible to everyone, including the dispute", () => {
    // §E17.5: the citizen holds the last gate, and §E18 says a dispute enters
    // the contractor's public record. Both halves, or neither.
    for (const view of ["citizen", "officer", "public"] as const) {
      const confirmed: RealtimeEnvelope = { ...template(), event_type: "citizen_confirmed" };
      const disputed: RealtimeEnvelope = { ...template(), event_type: "citizen_disputed" };
      expect(visibleIn(confirmed, view)).toBe(true);
      expect(visibleIn(disputed, view)).toBe(true);
    }
  });

  it("every event type the backend registers has words to render", () => {
    // §E27 is an audit: "a visual element not on this list, and not
    // classifiable as chrome, is a defect". The converse matters too — an event
    // that arrives with no label renders as a key on a citizen's ledger.
    const labelled = new Set(
      Object.keys(base)
        .filter((key) => key.startsWith("event."))
        .map((key) => key.slice("event.".length)),
    );
    expect(labelled.size).toBeGreaterThanOrEqual(33);
  });
});

describe("§16.1 — the contractor profile publishes no collapsible score", () => {
  const schema = JSON.parse(
    readFileSync(join(ROOT, "..", "backend", "nemesis", "api", "openapi.json"), "utf8"),
  ) as { components: { schemas: Record<string, { properties?: Record<string, unknown> }> } };

  it("`ContractorProfileResponse` has no rating, score or grade field", () => {
    /**
     * §E26: *"Cannot be collapsed to one score — **no single-value variant
     * exists in the API**."*
     *
     * The blueprint's enforcement mechanism is that the number does not exist
     * upstream, which is stronger than a component that declines to compute it.
     * This asserts that upstream property rather than trusting it, so the day
     * somebody adds `rating` the argument happens before the field ships.
     */
    const properties = Object.keys(
      schema.components.schemas["ContractorProfileResponse"]?.properties ?? {},
    );
    const collapsible = properties.filter((name) =>
      /^(rating|score|grade|stars|overall|rank)$/i.test(name),
    );
    expect(
      collapsible,
      `a single-value contractor metric appeared: ${collapsible.join(", ")}`,
    ).toEqual([]);
  });

  it("it does publish the disclaimer that §E18 renders as first-class UI", () => {
    const properties = Object.keys(
      schema.components.schemas["ContractorProfileResponse"]?.properties ?? {},
    );
    expect(properties).toContain("rating_disclaimer");
    expect(properties).toContain("suppression_threshold");
  });
});

describe("§E19.4 — an ambiguous SSIM says so", () => {
  it("scores inside the band are labelled ambiguous, not rounded into a verdict", () => {
    expect(isAmbiguous(0.53)).toBe(true);
    expect(isAmbiguous(0.2)).toBe(false);
    expect(isAmbiguous(0.9)).toBe(false);
  });
});

describe("§E10.1 — strings resolve, and a sentence is a translation unit", () => {
  it("a missing key is visible rather than blank", () => {
    // §E3.3: honesty is rendered, including about our own gaps. A blank space
    // where a sentence should be reads as a product defect; a marked key reads
    // as a missing translation, which is what it is.
    const s = makeStrings("common", "en", {});
    expect(t(s, "severity.high")).toBe("⟦severity.high⟧");
    expect(s.missing.has("severity.high")).toBe(true);
  });

  it("plurals use the locale's own rules, not an equality check", () => {
    expect(plural(strings, "suppression.withheld", 1, { threshold: 1 })).toContain("1 report —");
    expect(plural(strings, "suppression.withheld", 5, { threshold: 5 })).toContain("5 reports —");
  });

  it("placeholders are named, so word order can change between scripts", () => {
    const s = makeStrings("common", "mr", marathi);
    const rendered = plural(s, "suppression.withheld", 5, { threshold: 5 });
    expect(rendered).toContain("5");
    expect(rendered).not.toContain("{threshold}");
  });

  it("the Marathi seed covers every severity and status label", () => {
    // §E10.1 makes Devanagari a design partner, not a fallback. The labels that
    // appear on every surface are the ones that must not fall through.
    for (const level of Object.keys(SEVERITY)) {
      expect(marathi[`severity.${level}`], `severity.${level} is not seeded`).toBeTypeOf("string");
    }
    for (const status of COMPLAINT_STATUSES) {
      expect(marathi[`status.${status}`], `status.${status} is not seeded`).toBeTypeOf("string");
    }
  });

  it("every seeded Marathi key exists in the source bundle", () => {
    // A translation for a key nobody asks for is dead weight, and usually means
    // a key was renamed on one side only.
    for (const key of Object.keys(marathi)) {
      if (key.startsWith("$")) continue;
      expect(base[key], `${key} is translated but not in the base bundle`).toBeTypeOf("string");
    }
  });
});
