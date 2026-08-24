import type { components } from "@/generated/api";
import { REALTIME_SHAPED_EVENT_TYPES } from "@/generated/enums";

/**
 * The wire shapes — §26.3, ADR-0016.
 *
 * Every type here is an alias of a generated one. Nothing in this file
 * describes a backend contract; it only names them, which is the line
 * execution-plan Law 2 draws. If a field you need is not on `RealtimeEnvelope`,
 * the server does not publish it and the fix is in `nemesis/realtime/envelope.py`,
 * not here.
 */

export type RealtimeEnvelope = components["schemas"]["RealtimeEnvelope"];
export type RealtimeHeartbeat = components["schemas"]["RealtimeHeartbeat"];
export type RealtimeResyncRequired = components["schemas"]["RealtimeResyncRequired"];
export type ShapedEventType = components["schemas"]["RealtimeShapedEventType"];

/** Anything the socket can deliver. */
export type RealtimeMessage = RealtimeEnvelope | RealtimeHeartbeat | RealtimeResyncRequired;

const SHAPED = new Set<string>(REALTIME_SHAPED_EVENT_TYPES);

/**
 * **Realtime payloads are default-deny (ADR-0016).**
 *
 * Most registered event types arrive with an empty payload: the client learns
 * that something happened and nothing about what. That is a deliberate privacy
 * property, not a gap to work around — but a surface that assumes otherwise
 * renders an empty pin and looks broken, so the distinction is made explicit
 * here rather than discovered at runtime.
 *
 * §E27's traceability table maps twenty-four event types to visuals. Eight of
 * them carry a payload today. The rest are a signal to refetch, and
 * `reconcile.ts` is what turns that signal into data.
 */
export function carriesPayload(envelope: RealtimeEnvelope): boolean {
  return SHAPED.has(envelope.event_type);
}

export function isHeartbeat(message: RealtimeMessage): message is RealtimeHeartbeat {
  return message.event_type === "heartbeat";
}

export function isResyncRequired(message: RealtimeMessage): message is RealtimeResyncRequired {
  return message.event_type === "resync_required";
}

export function isEnvelope(message: RealtimeMessage): message is RealtimeEnvelope {
  return !isHeartbeat(message) && !isResyncRequired(message);
}

/**
 * Parse one frame.
 *
 * Returns `null` rather than throwing on anything unrecognisable. A stream is
 * not a request: one malformed frame must not take down a socket that is
 * otherwise delivering, and §E14.3's whole position is that the socket is a
 * hint — losing a hint is survivable because the read path is the authority.
 */
export function parseMessage(raw: string): RealtimeMessage | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return null;
    if (!("event_type" in value) || typeof value.event_type !== "string") return null;
    return value as RealtimeMessage;
  } catch {
    return null;
  }
}
