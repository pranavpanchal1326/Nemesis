import { describe, expect, it } from "vitest";

import {
  afterInput,
  afterTrigger,
  BOOLEAN_INPUTS,
  BOOLEANS_AT_REST,
  createMachine,
  EDGES,
  settle,
  STATES,
  TRIGGERS,
  type FigureState,
} from "../src/ink/machine.ts";
import { EVENT_TRIGGERS, triggerFor } from "../src/ink/live-ink.ts";
import { REALTIME_SHAPED_EVENT_TYPES } from "../src/generated/enums.ts";

/**
 * §E8.1's contract, asserted — F15, M9.6.
 *
 * The blueprint states the character as a list of input names and a chain of
 * state names. This file's first job is to hold the code to that list literally,
 * because ADR-0048 makes those names an interface with a document rather than
 * variables: if somebody renames `looking_down` to `lookingDown` for tidiness,
 * §E8.1 and the source have silently disagreed and nothing else would notice.
 *
 * Its second job is the half of F15's gate that does not need a browser: that
 * a **real backend event** is what fires `relief`, and that the event it fires
 * on is one this system actually publishes.
 */

describe("§E8.1 — the declared inputs and states, literally", () => {
  it("declares exactly the eight inputs §E8.1 names, spelled as §E8.1 spells them", () => {
    expect([...BOOLEAN_INPUTS, ...TRIGGERS]).toEqual([
      "walking",
      "stopped",
      "looking_down",
      "raise_phone",
      "shoulders_drop",
      "shutter",
      "relief",
      "disappointed",
    ]);
  });

  it("declares exactly the eight states, in the order the arrow chain reads", () => {
    expect(STATES).toEqual([
      "idle",
      "walk",
      "halt",
      "observe",
      "dejected",
      "report",
      "wait",
      "confirmed",
    ]);
  });

  it("has no edge to a state outside the declared set", () => {
    for (const edge of EDGES) {
      expect(STATES).toContain(edge.from);
      expect(STATES).toContain(edge.to);
    }
  });

  it("leaves no state a figure can enter and never leave", () => {
    // Every state must have at least one way out, or a figure that reaches it
    // is stuck on screen for the rest of the session. `confirmed` is the one
    // exception and it is deliberate: relief is where the story ends.
    const exits = new Set<FigureState>(EDGES.map((edge) => edge.from));
    for (const trigger of ["relief", "disappointed"] as const) {
      for (const state of STATES) {
        if (afterTrigger(state, trigger) !== state) exits.add(state);
      }
    }
    for (const state of STATES) {
      if (state === "confirmed") continue;
      expect(exits, `${state} has no exit`).toContain(state);
    }
  });
});

describe("§E8.1 — the walk, transition by transition", () => {
  it("walks the whole chain with nothing but inputs and triggers", () => {
    const machine = createMachine();
    expect(machine.snapshot().state).toBe("idle");

    machine.set("walking", true);
    expect(machine.snapshot().state).toBe("walk");

    machine.set("stopped", true);
    expect(machine.snapshot().state).toBe("halt");

    machine.set("looking_down", true);
    expect(machine.snapshot().state).toBe("observe");

    machine.fire("shoulders_drop");
    expect(machine.snapshot().state).toBe("dejected");

    machine.set("raise_phone", true);
    expect(machine.snapshot().state).toBe("report");

    machine.fire("shutter");
    expect(machine.snapshot().state).toBe("wait");

    machine.fire("relief");
    expect(machine.snapshot().state).toBe("confirmed");

    // Eight transitions for eight moves, and every one of them counted. The
    // count is the seam the E2E gate reads.
    expect(machine.snapshot().transitions).toBe(7);
  });

  it("is order-independent when two inputs arrive together", () => {
    // The film crosses an act boundary by setting both at once. Whichever key
    // the loop reads first, the answer is `halt`.
    expect(settle("walk", { ...BOOLEANS_AT_REST, walking: true, stopped: true })).toBe("halt");
    expect(settle("idle", { ...BOOLEANS_AT_REST, walking: true, stopped: true })).toBe("halt");
  });

  it("reads a claim before it reads the absence of one", () => {
    // **The defect the F15 gate found.** §E16 Act 2 declares *not walking,
    // stopped, looking down* in one statement. Settling `walking: false` first
    // takes `walk → idle`, and `idle` listens for neither of the other two — so
    // a figure loaded straight into Act 2 stood at the cold open. `stopped` is
    // a claim and `walking: false` is the absence of one, and the claim wins.
    expect(settle("walk", { ...BOOLEANS_AT_REST, stopped: true, looking_down: true })).toBe(
      "observe",
    );
  });

  it("does not oscillate between two edges that are both true", () => {
    // `walk --stopped--> halt` and `halt --walking--> walk` are both correct and
    // both fire at the Act 1/Act 2 boundary. Without the visited guard the
    // settle would swap between them until it ran out of passes and return
    // whichever it stopped on — which is a coin toss wearing a state machine.
    expect(settle("walk", { ...BOOLEANS_AT_REST, walking: true, stopped: true })).toBe("halt");
    expect(settle("halt", { ...BOOLEANS_AT_REST, walking: true })).toBe("walk");
  });

  it("applies a whole declaration as one statement", () => {
    // `apply` exists because four `set()` calls are four statements, and the
    // machine answers each one before it hears the next.
    const machine = createMachine("walk");
    machine.apply({ stopped: true, looking_down: true });
    expect(machine.snapshot().state).toBe("observe");

    // Absent keys are false: an act that stops declaring `walking` has stopped
    // walking, and a latched input is a figure still walking after the walk.
    const walking = createMachine("idle");
    walking.apply({ walking: true });
    expect(walking.snapshot().state).toBe("walk");
    walking.apply({});
    expect(walking.snapshot().state).toBe("idle");
  });

  it("catches a reader up through the acts they did not scroll", () => {
    // What `<StoryFigure>` does on a deep link to Act 4: replay each act's
    // declaration in order. Not a seek — every step is a real input the machine
    // answers, which is why ADR-0041's property survives it.
    const machine = createMachine("idle");
    machine.apply({}); // cold open
    machine.apply({ walking: true }); // the walk
    machine.apply({ stopped: true, looking_down: true }); // the stop
    machine.apply({ looking_down: true }); // the silence…
    machine.fire("shoulders_drop"); // …and its beat
    machine.apply({ looking_down: true, raise_phone: true }); // the report
    expect(machine.snapshot().state).toBe("report");
  });

  it("ignores an input the current state has no opinion about", () => {
    expect(afterInput("walk", "looking_down", true)).toBe("walk");
    expect(afterInput("confirmed", "walking", true)).toBe("confirmed");
  });

  it("does not notify a subscriber when nothing changed", () => {
    const machine = createMachine();
    let calls = 0;
    machine.subscribe(() => {
      calls += 1;
    });
    machine.set("looking_down", true);
    machine.set("looking_down", true);
    expect(calls).toBe(0);
    machine.set("walking", true);
    expect(calls).toBe(1);
  });
});

describe("§E8.1 — relief and disappointment arrive from anywhere", () => {
  it("fires relief from every state, because the event does not wait for the walk", () => {
    for (const state of STATES) {
      if (state === "confirmed") continue;
      expect(afterTrigger(state, "relief"), state).toBe("confirmed");
    }
  });

  it("refuses to un-confirm a confirmed figure", () => {
    // A confirmation a later event reverses is `citizen_disputed`, which is a
    // different event on a different entity and is not this trigger.
    expect(afterTrigger("confirmed", "disappointed")).toBe("confirmed");
    expect(afterTrigger("confirmed", "relief")).toBe("confirmed");
  });

  it("keeps the narrative triggers local to their beat", () => {
    // `shutter` out of `idle` must do nothing: the shutter is a beat inside the
    // report, and a trigger that worked from anywhere would let any surface
    // teleport a figure into `wait`.
    expect(afterTrigger("idle", "shutter")).toBe("idle");
    expect(afterTrigger("walk", "shoulders_drop")).toBe("walk");
  });
});

describe("F15's gate, the half that needs no browser", () => {
  it("binds relief to citizen_confirmed, which is what §E8.1 says", () => {
    expect(triggerFor("citizen_confirmed")).toBe("relief");
  });

  it("binds every trigger it binds to an event this system actually publishes", () => {
    // The whole force of *"the character reacts to real backend events"* is
    // that the left-hand column of that table is real. An event type that is
    // not in the published shaped set is a binding that can never fire, which
    // would be a character wired to nothing while claiming otherwise.
    for (const eventType of Object.keys(EVENT_TRIGGERS)) {
      expect(REALTIME_SHAPED_EVENT_TYPES as readonly string[], eventType).toContain(eventType);
    }
  });

  it("moves a figure when a real event fires, and records that it moved", () => {
    const machine = createMachine("wait");
    const before = machine.snapshot().transitions;
    const trigger = triggerFor("citizen_confirmed");
    expect(trigger).not.toBeNull();
    if (trigger !== null) machine.fire(trigger);
    expect(machine.snapshot().state).toBe("confirmed");
    expect(machine.snapshot().transitions).toBe(before + 1);
  });

  it("says nothing about the events that say nothing about a person", () => {
    // §E3.4: a vocabulary that means two things means nothing. Most of the
    // stream is about clusters, severities and work orders, and inventing a
    // pose for each would be exactly that failure.
    expect(triggerFor("cluster_match_found")).toBeNull();
    expect(triggerFor("severity_scored")).toBeNull();
    expect(triggerFor("work_order_created")).toBeNull();
  });
});
