"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { t, type Strings } from "@/lib/i18n/strings";
import { steppedClock } from "@/lib/stepped-clock";

import { drawInk, DEFAULT_STYLE, type InkStyle } from "./draw";
import { drawingFor, type FigureName } from "./figures";
import { bindMachineToBus } from "./live-ink";
import {
  createMachine,
  type BooleanInput,
  type FigureState,
  type Machine,
  type MachineSnapshot,
} from "./machine";
import "./ink.css";

/** Stable identity, so the effect below does not re-run on every render of a
 *  caller that declares no inputs at all. */
const EMPTY_INPUTS: Partial<Record<BooleanInput, boolean>> = {};

/**
 * One figure on a surface — §E8, F15.
 *
 * The component owns three things and deliberately no more: a canvas, a
 * subscription to the 12 fps stepped clock, and (optionally) a subscription to
 * the event bus. The machine is `machine.ts`, the drawing is `figures.ts`, the
 * ink is `draw.ts`, and none of the three knows this file exists.
 *
 * **React re-renders when the *state* changes, not when the frame does.** A
 * figure animating on twos at 12 fps would be six renders a second per figure,
 * and a console empty state can carry one while a table of four hundred rows is
 * live. So the clock writes straight to the canvas — §E14.2's *"transient
 * subscriptions that drive shader uniforms without a React re-render"*, applied
 * to a 2D context — and React only hears about the machine's state because the
 * accessible label has to change with it.
 *
 * **The canvas is `role="img"` with a sentence, not a decoration.** §E22 makes
 * the accessible peer a peer: somebody who cannot see the figure is told which
 * of §E8.2's people it is and what they are doing, in their own locale, and
 * that sentence changes when the machine moves. §E13 Tier D — no JavaScript —
 * renders no canvas at all, and loses only the drawing: every act and every
 * empty state that mounts a figure carries its own copy, which is where the
 * meaning lives.
 *
 * **Nothing here can start an animation.** There is no `play`, no timeline, no
 * duration and no `requestAnimationFrame` that this component owns. It draws
 * the frame the clock is on, in the state the machine is in, and that is the
 * entirety of the character layer's motion (ADR-0041, ADR-0048).
 */
export function InkFigure({
  strings,
  figure,
  machine: supplied,
  inputs,
  live = false,
  entityId = null,
  initial = "idle",
  fill = 0.86,
  style = DEFAULT_STYLE,
  className,
  onState,
}: {
  readonly strings: Strings;
  readonly figure: FigureName;
  /**
   * A machine the caller made, so the caller can fire a trigger.
   *
   * Only one surface needs this: §E16 Act 2, whose `shoulders_drop` is a
   * narrative beat rather than an event (see `live-ink.ts`). Everything else
   * lets the component own its machine and drives it through `inputs`.
   */
  readonly machine?: Machine;
  /** §E8.1's boolean inputs, as the surface currently understands them. */
  readonly inputs?: Partial<Record<BooleanInput, boolean>>;
  /** Bind the machine's triggers to the realtime bus. */
  readonly live?: boolean;
  /** Which entity's events this figure answers to. See `BindOptions`. */
  readonly entityId?: string | null;
  readonly initial?: FigureState;
  /** Share of the canvas height the figure's crown reaches. */
  readonly fill?: number;
  readonly style?: InkStyle;
  readonly className?: string;
  readonly onState?: (state: FigureState) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // `useState` rather than `useRef` for the fallback machine: a machine created
  // in a ref initialiser is created on every render and thrown away, which is
  // free for an object and not free for one that has subscribers.
  const [fallback] = useState<Machine>(() => createMachine(initial));
  const machine = supplied ?? fallback;

  // --- the machine's state, published to React and to the DOM --------------
  // `useSyncExternalStore` rather than an effect that calls `setState`: the
  // machine *is* an external store, and reading it in an effect would render
  // once with a stale state and again with the right one. `Machine.snapshot()`
  // returns by identity precisely so this works — see its own note. The server
  // snapshot is the same function: a machine has no browser in it.
  const snapshot: MachineSnapshot = useSyncExternalStore(
    machine.subscribe,
    machine.snapshot,
    machine.snapshot,
  );

  useEffect(() => {
    onState?.(snapshot.state);
  }, [onState, snapshot.state]);

  // --- the surface's boolean inputs ---------------------------------------
  // `Machine.set` ignores a write that does not change the value, so this is
  // idempotent and costs nothing when the parent re-renders for an unrelated
  // reason.
  const declared = useMemo(() => inputs ?? EMPTY_INPUTS, [inputs]);
  useEffect(() => {
    // One statement, settled once. See `Machine.apply` for why four `set()`
    // calls is not the same thing.
    machine.apply(declared);
  }, [declared, machine]);

  // --- the bus -------------------------------------------------------------
  useEffect(() => {
    if (!live) return;
    return bindMachineToBus(machine, { entityId });
  }, [live, machine, entityId]);

  // --- the clock, and the drawing -----------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;

    let step = steppedClock.step;

    const paint = (): void => {
      const context = canvas.getContext("2d");
      if (context === null) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 3);
      const widthPx = canvas.clientWidth;
      const heightPx = canvas.clientHeight;
      if (widthPx === 0 || heightPx === 0) return;
      // Assigning the same value would reset the backing store and clear the
      // canvas on every frame anyway, so the guard is about flicker on
      // Safari rather than about arithmetic.
      const backingWidth = Math.round(widthPx * dpr);
      const backingHeight = Math.round(heightPx * dpr);
      if (canvas.width !== backingWidth) canvas.width = backingWidth;
      if (canvas.height !== backingHeight) canvas.height = backingHeight;

      drawInk(
        context,
        drawingFor(figure, machine.snapshot().state, Math.max(step, 0)),
        { widthPx, heightPx, dpr, fill },
        Math.max(step, 0),
        style,
      );
    };

    paint();

    const unsubscribeClock = steppedClock.subscribe((current) => {
      step = current;
      paint();
    });
    // A state change repaints immediately rather than waiting up to 83 ms for
    // the next frame. `relief` landing a twelfth of a second late is invisible;
    // a figure that has not moved when the assertion reads it is a flaky gate.
    const unsubscribeMachine = machine.subscribe(paint);

    const observer = new ResizeObserver(paint);
    observer.observe(canvas);

    return () => {
      unsubscribeClock();
      unsubscribeMachine();
      observer.disconnect();
    };
  }, [figure, machine, fill, style]);

  const described = t(strings, "ink.described", {
    figure: t(strings, `ink.figure.${figure}`),
    doing: t(strings, `ink.state.${snapshot.state}`),
  });

  return (
    <div
      className={className === undefined ? "ink" : `ink ${className}`}
      data-ink-figure={figure}
      data-ink-state={snapshot.state}
      // The seam the gate reads: *did a real event move this figure?* A state
      // name cannot answer that on its own — see `MachineSnapshot.transitions`.
      data-ink-transitions={String(snapshot.transitions)}
      // The description sits on the wrapper and the canvas is hidden, which is
      // the shape `<ClayScene>` already uses: a `<canvas>` is an interactive
      // element to the accessibility tree, and giving an interactive element a
      // non-interactive role is a lint failure and a real ambiguity for a
      // screen reader.
      role="img"
      aria-label={described}
    >
      <canvas ref={canvasRef} className="ink__canvas" aria-hidden="true" />
    </div>
  );
}
