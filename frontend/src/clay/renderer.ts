"use client";

/**
 * The renderer, and the two things it is allowed to tell us — M8.1, ADR-0037.
 *
 * `WebGPURenderer` in **every** tier. The WebGL 2 backend is three.js's own
 * automatic fallback, taken inside the constructor, and Tiers S and A are what
 * that choice is *called* afterwards — never a branch of ours. This module is
 * the seam where that inversion is made concrete: it constructs one renderer,
 * asks it which backend it took, and hands both back.
 *
 * **`forceWebGL` exists, and it is not a hedge.** Phase 19's gate says:
 *
 * > the WebGL 2 backend fallback renders the same scene as WebGPU, verified by
 * > golden image.
 *
 * That gate is unassertable on a machine with an adapter unless the WebGL 2
 * path can be *asked for*. So the flag is reachable — from a query parameter,
 * on a dev-only proof route, and from Playwright. It is never reachable from
 * product code, which is the distinction between a test seam and a branch.
 *
 * **Context loss is wired here rather than in a component** because the
 * listener has to exist before the first frame and has to survive a React
 * re-render. `preventDefault()` on `webglcontextlost` is the entire reason the
 * restore path can work at all: without it the browser never fires
 * `webglcontextrestored`, and §E13's *"reconstructs the scene without a page
 * reload"* becomes unreachable no matter how good the rebuild code is. The
 * WebGPU backend has its own loss story (`device.lost`), and both are attached,
 * because a gate that only holds on one backend holds on neither.
 */

import { WebGPURenderer } from "three/webgpu";

import type { Backend } from "./tier";

export interface ClayRendererOptions {
  readonly canvas: HTMLCanvasElement | OffscreenCanvas;
  /** Ask for the WebGL 2 backend even where WebGPU exists. Gate-only. */
  readonly forceWebGL?: boolean;
  /** Fired on the first and every subsequent loss. */
  readonly onContextLost?: () => void;
  readonly onContextRestored?: () => void;
  /** Antialiasing is a tier decision, not a constructor default. */
  readonly antialias?: boolean;
}

export interface ClayRenderer {
  readonly renderer: WebGPURenderer;
  readonly backend: Backend;
  /** Detach the loss listeners. Disposing the renderer is the caller's. */
  readonly detach: () => void;
}

/**
 * Construct and initialise. Async because `WebGPURenderer.init()` is: adapter
 * acquisition is a promise, and a synchronous constructor that renders one
 * black frame before the device arrives is the flicker every WebGPU integration
 * ships first and removes later.
 */
export async function createClayRenderer(options: ClayRendererOptions): Promise<ClayRenderer> {
  const renderer = new WebGPURenderer({
    canvas: options.canvas,
    antialias: options.antialias ?? true,
    // The frame is composited by the press and the lens stack, which read it
    // as a texture. An opaque default framebuffer is one fewer blend the
    // compositor has to do, and the sheet is always the ground anyway (§E9.3).
    alpha: false,
    forceWebGL: options.forceWebGL ?? false,
  });

  await renderer.init();

  const detach = attachLossListeners(renderer, options);
  return { renderer, backend: backendOf(renderer), detach };
}

/**
 * Which backend the renderer actually took.
 *
 * A property test rather than a capability test: `navigator.gpu` being present
 * says nothing about whether the adapter request succeeded, and this is asked
 * *after* `init()` precisely so the answer is the fact rather than the forecast.
 */
export function backendOf(renderer: WebGPURenderer): Backend {
  const backend: object = renderer.backend;
  return "isWebGPUBackend" in backend && backend.isWebGPUBackend === true ? "webgpu" : "webgl2";
}

/**
 * A snapshot of what the last frame cost — §E23, and the numbers CI asserts.
 *
 * Both come from `renderer.info`, which is three.js's own accounting rather
 * than an estimate of ours. `memoryBytes` is the sum the renderer keeps of
 * every attribute, texture, render target and uniform buffer it has allocated:
 * it is the honest answer to *"how much of this device's memory is the map
 * holding"*, which is the question ADR-0002 makes load-bearing — the
 * Investigation Agent shares this GPU and must never be starved by the city.
 */
export interface FrameStats {
  readonly drawCalls: number;
  readonly triangles: number;
  readonly memoryBytes: number;
  readonly geometries: number;
  readonly textures: number;
}

export function frameStats(renderer: WebGPURenderer): FrameStats {
  const info = renderer.info;
  return {
    drawCalls: info.render.drawCalls,
    triangles: info.render.triangles,
    memoryBytes: info.memory.total,
    geometries: info.memory.geometries,
    textures: info.memory.textures,
  };
}

export const BYTES_PER_MB = 1024 * 1024;

function attachLossListeners(renderer: WebGPURenderer, options: ClayRendererOptions): () => void {
  const element = renderer.domElement;
  const lost = options.onContextLost;
  const restored = options.onContextRestored;

  const onLost = (event: Event) => {
    // Without this the browser treats the loss as final and never fires
    // `webglcontextrestored`. Every line of the rebuild path downstream is
    // dead code if this one is missing, which is why it is not conditional on
    // a handler being supplied.
    event.preventDefault();
    lost?.();
  };
  const onRestored = () => {
    restored?.();
  };

  element.addEventListener("webglcontextlost", onLost);
  element.addEventListener("webglcontextrestored", onRestored);

  // The WebGPU backend does not raise those events. Its equivalent is a
  // promise on the device that resolves when the device goes away — same
  // situation, different API, and §E13's rule is about the situation.
  const device: unknown = (renderer.backend as { device?: unknown }).device;
  if (isGpuDevice(device)) {
    void device.lost.then(() => {
      lost?.();
    });
  }

  return () => {
    element.removeEventListener("webglcontextlost", onLost);
    element.removeEventListener("webglcontextrestored", onRestored);
  };
}

function isGpuDevice(value: unknown): value is { lost: Promise<unknown> } {
  return (
    typeof value === "object" && value !== null && "lost" in value && typeof value.lost === "object"
  );
}
