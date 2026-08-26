"use client";

import { useEffect, useRef, useState } from "react";
import { useStore } from "zustand/react";

import { InkFigure } from "@/ink/InkFigure";
import { createMachine, type BooleanInput, type FigureState } from "@/ink/machine";
import type { Strings } from "@/lib/i18n/strings";

import { ACT_IDS, type ActId } from "./acts";
import { storyLive } from "./live-story";

/**
 * The Reporter, walking the film — §E8.2, §E16, F15.
 *
 * §E16's first five acts are a person: they walk, they stop, they look down,
 * their shoulders drop, they raise a phone. Until F15 that person was described
 * in the copy and absent from the screen. This component is the binding between
 * the act the spine is on and §E8.1's inputs, and it is deliberately a table
 * rather than a sequence — an act does not *play* the figure, it *states* what
 * is true of them, and the machine works out what state that leaves them in.
 *
 * **A reader who arrives mid-film is caught up, not teleported.** §E8.1's
 * states are a chain — `idle → walk → halt → observe → …` — so a machine told
 * only *"stopped, looking down"* while sitting in `idle` correctly stays in
 * `idle`: nothing ever told it the walk happened. **This was a real defect and
 * the F15 gate found it**: opening the film at Act 2 by deep link, or reloading
 * mid-scroll, left the figure standing at the cold open while the copy said
 * they had stopped. The fix is the catch-up loop below, and the important thing
 * about it is what it is *not* — it does not set a state, it replays the *declarations*
 * of the acts in between, in order, through the same `set()` and `fire()` any
 * reader's scroll would have used. The machine still decides. ADR-0041's
 * property is untouched: there is no state to jump to and no timeline to seek.
 *
 * **The one trigger fired here is `shoulders_drop`, and it is narrative.**
 * `live-ink.ts` explains why that is the exception: §E16 Act 3 is the film's
 * disappointment beat and the film already labels that act `data-real="false"`
 * in the DOM. Every other trigger — `shutter`, `relief`, `disappointed` — comes
 * off the bus, which is why this figure is mounted `live`.
 *
 * **The figure leaves after Act 5, and that is §E16's staging rather than a
 * shortcut.** Acts 6–9 pull back to the city, the survey frame and the
 * workbench; a road-level ink figure standing in those shots would be a person
 * the size of a ward. What happens to their report after they walk away is the
 * rest of the film, and the pipeline is the character in it.
 */

/** Which acts the figure is on stage for. */
const ON_STAGE: ReadonlySet<ActId> = new Set<ActId>([
  "cold-open",
  "walk",
  "stop",
  "silence",
  "report",
  "pipeline",
]);

/**
 * §E8.1's boolean inputs, per act.
 *
 * Absent keys are `false`. Read down the column and it is §E16's own table: the
 * walk walks, the stop stops and looks down, the silence holds, the report
 * raises the phone.
 */
const ACT_INPUTS: Readonly<Partial<Record<ActId, Partial<Record<BooleanInput, boolean>>>>> = {
  walk: { walking: true },
  stop: { stopped: true, looking_down: true },
  silence: { looking_down: true },
  report: { looking_down: true, raise_phone: true },
  pipeline: { raise_phone: true },
};

const AT_REST: Partial<Record<BooleanInput, boolean>> = {};

export function StoryFigure({
  strings,
  act,
  onState,
}: {
  readonly strings: Strings;
  readonly act: ActId;
  readonly onState?: (state: FigureState) => void;
}) {
  const [machine] = useState(() => createMachine("idle"));

  /**
   * The last act whose declarations have been applied.
   *
   * `-1` means none — a fresh mount, which is either the top of the film or a
   * reader arriving in the middle of it. Either way the answer is the same:
   * apply everything up to where they are.
   */
  const applied = useRef(-1);

  /**
   * The report the film is following, once the reader has filed one.
   *
   * Read from the film's own store rather than passed down, because it is the
   * same fact Acts 5 and 6 read and one source is one source. It scopes the bus
   * binding: this figure is the person who filed *this* report, so it must not
   * relax because somebody else's complaint was confirmed. Null before Act 4
   * has produced anything, in which case the figure hears the whole stream —
   * correct for a film nobody has yet reported into, and the state a visitor
   * spends most of the walk in.
   */
  const complaintId = useStore(storyLive, (state) => state.complaintId);

  useEffect(() => {
    const target = ACT_IDS.indexOf(act);
    if (target < 0) return;

    if (target > applied.current) {
      // Forwards: replay every act's declarations between where the figure was
      // and where the reader is. One pass per act, in order, exactly as a
      // scroll would have delivered them.
      for (let index = applied.current + 1; index <= target; index += 1) {
        declare(ACT_IDS[index]);
      }
    } else if (target < applied.current) {
      // Backwards. Not replayed — scrolling up does not un-drop a pair of
      // shoulders, and a figure that reset itself every time a reader looked
      // back at Act 1 would be a figure with no memory of the film they are in.
      // The act's own declarations still apply, so the pose is right.
      declare(act);
    }
    applied.current = target;

    function declare(id: ActId | undefined): void {
      if (id === undefined) return;
      // One statement, settled once — see `Machine.apply`. Four separate
      // writes is the defect this phase's gate found: `walking: false` lands
      // first and drops the figure to `idle` before `stopped: true` is read.
      machine.apply(ACT_INPUTS[id] ?? AT_REST);
      // §E16 Act 3's beat, fired on the act it belongs to — including when it
      // is passed through on the way to a later act, which is what makes a deep
      // link to Act 4 show a figure who has already been disappointed.
      if (id === "silence") machine.fire("shoulders_drop");
    }
  }, [act, machine]);

  if (!ON_STAGE.has(act)) return null;

  return (
    <InkFigure
      strings={strings}
      figure="reporter"
      className="ink--act"
      machine={machine}
      live
      entityId={complaintId}
      {...(onState === undefined ? {} : { onState })}
    />
  );
}
