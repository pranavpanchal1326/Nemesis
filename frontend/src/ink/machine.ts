/**
 * The character, as §E8.1 specifies it — F15, M9.6.
 *
 *     inputs  walking(bool) · stopped(bool) · looking_down(bool) · shoulders_drop(trigger)
 *             raise_phone(bool) · shutter(trigger) · relief(trigger) · disappointed(trigger)
 *     states  idle → walk → halt → observe → dejected → report → wait → confirmed
 *
 * That block is the whole contract, and this module is it in TypeScript.
 * ADR-0041 decided *why* — a character bound to the event log rather than a
 * timeline — and ADR-0048 decided that the contract is this machine rather than
 * a `.riv` file, so the names below are §E8.1's names character for character,
 * `snake_case` and all. They are an interface with a document and with any
 * future artefact that implements it, not local variables, and the F15 audit
 * greps for exactly these words.
 *
 * **There is nothing here that can be played.** No duration, no easing, no
 * frame index, no clock. A caller sets an input or fires a trigger and asks
 * what state that leaves the figure in. Everything that *moves* is a pure
 * function of the answer plus the stepped clock (`figures.ts`), which is what
 * makes the Phase 20 gate — *a scene that can only be fired by a button fails*
 * — hold by construction rather than by discipline: there is no play call for a
 * button to make.
 *
 * **Two of the four triggers are global and two are local, and the split is a
 * decision.** `shoulders_drop` and `shutter` are beats *inside* the walk —
 * §E16's Act 2 and Act 4 — and they mean nothing anywhere else, so they are
 * edges from one state. `relief` and `disappointed` are facts about the
 * citizen's report, arriving from the backend whenever the backend has them:
 * `citizen_confirmed` can land while the figure is idle on a console empty
 * state, hours after the walk. A machine that could only receive them from
 * `wait` would be a timeline with extra steps — it would require the figure to
 * have been walked into position first, which is precisely the property
 * ADR-0041 exists to forbid.
 */

/** §E8.1's eight states, in the order the arrow chain lists them. */
export const STATES = [
  "idle",
  "walk",
  "halt",
  "observe",
  "dejected",
  "report",
  "wait",
  "confirmed",
] as const;

export type FigureState = (typeof STATES)[number];

/** §E8.1's four boolean inputs. */
export const BOOLEAN_INPUTS = ["walking", "stopped", "looking_down", "raise_phone"] as const;

export type BooleanInput = (typeof BOOLEAN_INPUTS)[number];

/** §E8.1's four triggers. */
export const TRIGGERS = ["shoulders_drop", "shutter", "relief", "disappointed"] as const;

export type Trigger = (typeof TRIGGERS)[number];

/** Every boolean input's current value. All false is a figure standing still. */
export type Booleans = Readonly<Record<BooleanInput, boolean>>;

export const BOOLEANS_AT_REST: Booleans = {
  walking: false,
  stopped: false,
  looking_down: false,
  raise_phone: false,
};

/**
 * One edge.
 *
 * `when` is present on a boolean edge and absent on a trigger edge, which is
 * the only structural difference between the two — a trigger has no value to
 * compare against, it either fired or it did not.
 */
interface Edge {
  readonly from: FigureState;
  readonly on: BooleanInput | Trigger;
  readonly when?: boolean;
  readonly to: FigureState;
}

/**
 * The transition table.
 *
 * Ordered as §E8.1's arrow chain reads, with the two edges that leave the chain
 * — `walk → idle` when the walking stops without a stop being announced, and
 * `halt → walk` when it resumes — written down rather than left implicit. A
 * state a figure can enter and never leave is a figure stuck on screen, and
 * "the input went false again" is the commonest way that happens.
 */
export const EDGES: readonly Edge[] = [
  { from: "idle", on: "walking", when: true, to: "walk" },
  { from: "walk", on: "walking", when: false, to: "idle" },
  { from: "walk", on: "stopped", when: true, to: "halt" },
  { from: "halt", on: "walking", when: true, to: "walk" },
  { from: "halt", on: "looking_down", when: true, to: "observe" },
  { from: "observe", on: "shoulders_drop", to: "dejected" },
  { from: "dejected", on: "raise_phone", when: true, to: "report" },
  { from: "report", on: "shutter", to: "wait" },
];

/**
 * The two triggers that are edges from *anywhere*, and where they land.
 *
 * See the module note. `confirmed` is terminal for `relief` — a figure already
 * relieved is not relieved again — and `disappointed` is refused out of
 * `confirmed`, because a confirmation that a later event un-confirms is a
 * dispute (`citizen_disputed`), which is a different event on a different
 * entity and is not this trigger.
 */
const GLOBAL_TRIGGERS: Readonly<Record<"relief" | "disappointed", FigureState>> = {
  relief: "confirmed",
  disappointed: "dejected",
};

/**
 * The state a boolean input leaves the figure in.
 *
 * Pure, total, and it returns `from` unchanged when no edge matches — an input
 * a state does not listen for is not an error, it is a machine being told
 * something it has no opinion about. `looking_down` while walking is exactly
 * that, and it happens constantly.
 */
export function afterInput(from: FigureState, input: BooleanInput, value: boolean): FigureState {
  const edge = EDGES.find((e) => e.from === from && e.on === input && e.when === value);
  return edge?.to ?? from;
}

/** The state a trigger leaves the figure in. Same totality, same reasoning. */
export function afterTrigger(from: FigureState, trigger: Trigger): FigureState {
  if (trigger === "relief" || trigger === "disappointed") {
    if (from === "confirmed") return from;
    return GLOBAL_TRIGGERS[trigger];
  }
  const edge = EDGES.find((e) => e.from === from && e.on === trigger);
  return edge?.to ?? from;
}

/**
 * The order `settle()` considers inputs in — **not** `BOOLEAN_INPUTS` order.
 *
 * `BOOLEAN_INPUTS` is §E8.1's declared order and is the contract; this is a
 * *precedence*, and the two are different things that happened to look alike
 * until the F15 gate separated them.
 *
 * **The rule: a claim outranks the absence of a claim.** `stopped: true` says
 * something happened; `walking: false` says only that something is no longer
 * true. Settling the absence first is what produced the defect the gate found —
 * a figure told *"not walking, stopped, looking down"* in one breath took
 * `walk → idle` on the first input and then sat in `idle`, because `idle`
 * listens for neither of the other two. Taking `stopped` first gives
 * `walk → halt → observe`, which is what the reader is being told is happening.
 */
const SETTLE_ORDER = ["stopped", "looking_down", "raise_phone", "walking"] as const;

/**
 * Settle a whole input set against a state.
 *
 * Applied in `SETTLE_ORDER` and repeatedly until nothing more changes, because
 * one pass is not enough: a figure told `walking = true` and `stopped = true`
 * in the same breath — the boundary between the film's Act 1 and Act 2 — must
 * end at `halt`, and reaching `halt` needs `walk` to exist first.
 *
 * **A state is never re-entered inside one settle**, and that guard is what
 * makes the repetition safe rather than oscillating. `walk --stopped--> halt`
 * and `halt --walking--> walk` are both correct edges and both true at that
 * boundary; without the guard the loop would swap between them until it ran out
 * of passes and return whichever it happened to stop on. With it, the first
 * reading wins and the second is recognised as the round trip it is.
 */
export function settle(from: FigureState, booleans: Booleans): FigureState {
  let state = from;
  const visited = new Set<FigureState>([from]);

  // Bounded by the state count: every pass either enters a state never entered
  // in this settle or changes nothing, so there cannot be more useful passes
  // than there are states.
  for (const _pass of STATES) {
    let moved = false;
    for (const input of SETTLE_ORDER) {
      const next = afterInput(state, input, booleans[input]);
      if (next === state || visited.has(next)) continue;
      state = next;
      visited.add(next);
      moved = true;
    }
    if (!moved) return state;
  }
  return state;
}

export interface MachineSnapshot {
  readonly state: FigureState;
  readonly booleans: Booleans;
  /**
   * How many transitions this machine has taken.
   *
   * The seam the E2E gate reads. *"A real `citizen_confirmed` moves a
   * character"* is asserted by watching this number change on a figure nobody
   * touched — a state name alone cannot distinguish "moved to confirmed" from
   * "was rendered confirmed", and that difference is the whole gate.
   */
  readonly transitions: number;
}

export interface Machine {
  /**
   * The current snapshot, **by identity**.
   *
   * Returns the same object until the machine actually moves, which is what
   * lets `<InkFigure>` read it through `useSyncExternalStore` — that hook
   * compares snapshots by reference and a fresh object every call is an
   * infinite render loop. Cached rather than recomputed for exactly that
   * reason, and not as an optimisation.
   */
  snapshot: () => MachineSnapshot;
  /** Set one of §E8.1's boolean inputs. */
  set: (input: BooleanInput, value: boolean) => void;
  /**
   * Set every boolean input at once, and settle once.
   *
   * **Not a convenience over four `set()` calls, and the difference is the
   * defect the F15 gate found.** `set()` settles after each input, so writing
   * `walking: false` and then `stopped: true` takes `walk → idle` on the first
   * write and finds nothing to do on the second — `idle` listens for neither.
   * A surface that declares what is true of a figure *now* is making one
   * statement, not four, and `settle()`'s precedence only means anything when
   * it can see the whole statement.
   *
   * Absent keys are `false`: a declaration that stops mentioning `walking`
   * means the walking has stopped, and leaving the previous value latched is a
   * figure that keeps walking after the walk.
   */
  apply: (booleans: Partial<Booleans>) => void;
  /** Fire one of §E8.1's triggers. */
  fire: (trigger: Trigger) => void;
  /** Notified on every *change*, never on a no-op write. */
  subscribe: (listener: (snapshot: MachineSnapshot) => void) => () => void;
}

/**
 * One figure's machine.
 *
 * A hand-written store rather than Zustand, and the reason is scale rather than
 * taste: a surface can carry several figures, each one is four booleans and a
 * state name, and the store's job is to notify a canvas rather than to be read
 * by React. `live-ink.ts` is what connects one of these to the event bus.
 */
export function createMachine(initial: FigureState = "idle"): Machine {
  let state = initial;
  let booleans: Booleans = BOOLEANS_AT_REST;
  let transitions = 0;
  let current: MachineSnapshot = { state, booleans, transitions };
  const listeners = new Set<(snapshot: MachineSnapshot) => void>();

  const snapshot = (): MachineSnapshot => current;

  function commit(next: FigureState): void {
    if (next === state) return;
    state = next;
    transitions += 1;
    current = { state, booleans, transitions };
    for (const listener of listeners) listener(current);
  }

  return {
    snapshot,
    set: (input, value) => {
      if (booleans[input] === value) return;
      booleans = { ...booleans, [input]: value };
      commit(settle(state, booleans));
    },
    apply: (declared) => {
      const next: Booleans = {
        walking: declared.walking ?? false,
        stopped: declared.stopped ?? false,
        looking_down: declared.looking_down ?? false,
        raise_phone: declared.raise_phone ?? false,
      };
      const unchanged = BOOLEAN_INPUTS.every((input) => booleans[input] === next[input]);
      if (unchanged) return;
      booleans = next;
      commit(settle(state, booleans));
    },
    fire: (trigger) => {
      commit(afterTrigger(state, trigger));
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
