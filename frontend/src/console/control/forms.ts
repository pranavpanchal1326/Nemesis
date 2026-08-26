"use client";

import { useState } from "react";

/**
 * One administrative write, and what the screen says about it.
 *
 * Three forms on the control-plane screen do the same four things — send, say
 * "sending", say what came back, and say it in the server's own words when the
 * server refused — and they were three copies of it before this hook.
 *
 * **The refusal is the server's sentence, not ours.** `errors.py` writes
 * problem documents an administrator can act on: *"a taxonomy key must be
 * unique within the tenant"*, *"that slug is taken"*. Substituting *"that did
 * not work"* would delete the only useful part. This is the same exception the
 * activation route makes, for the same reason and the same audience — every
 * caller here is behind the control-plane token, so the reader is somebody the
 * deployment already trusts with the control plane.
 *
 * **No optimistic state.** A provisioned tenant, a defined category and a
 * published city are all things somebody else can observe a second later; a
 * screen that showed them as done before the server agreed would be a screen
 * that occasionally lies about a published city, which is ADR-0046's whole
 * subject.
 */
export type WriteState =
  | { readonly kind: "idle" }
  | { readonly kind: "sending" }
  | { readonly kind: "done"; readonly body: unknown }
  /** The server's own sentence, where it gave one. */
  | { readonly kind: "refused"; readonly detail: string | null };

export function useWrite(): {
  readonly state: WriteState;
  readonly send: (path: string, body: unknown, method?: "POST" | "PUT") => Promise<void>;
} {
  const [state, setState] = useState<WriteState>({ kind: "idle" });

  async function send(path: string, body: unknown, method: "POST" | "PUT" = "POST") {
    setState({ kind: "sending" });
    try {
      const response = await fetch(path, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const parsed: unknown = await response.json().catch(() => null);
      setState(
        response.ok
          ? { kind: "done", body: parsed }
          : { kind: "refused", detail: detailOf(parsed) },
      );
    } catch {
      // A network failure, not a refusal. `detail: null` renders the screen's
      // own sentence, because there is no server sentence to render.
      setState({ kind: "refused", detail: null });
    }
  }

  return { state, send };
}

/** RFC 9457's operator-facing sentence, preferring `detail` over `title` for
 *  the reason `errors.py` puts the explanation there. */
export function detailOf(body: unknown): string | null {
  if (typeof body !== "object" || body === null) return null;
  const document = body as Record<string, unknown>;
  for (const field of ["detail", "title"] as const) {
    const value = document[field];
    if (typeof value === "string" && value !== "") return value;
  }
  return null;
}
