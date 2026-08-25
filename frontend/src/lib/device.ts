"use client";

/**
 * The device fingerprint §26.1 requires — and what it deliberately is not.
 *
 * `POST /complaints` takes a required `device_fingerprint`. §11.3 uses it as
 * the unit of coordinated-abuse detection, and `nemesis/api/v1/complaints.py`
 * uses it as the rate limiter's identity: *"the fingerprint is the better key —
 * §11.3 already treats it as the unit of abuse."*
 *
 * **It is a random identifier this browser stores for itself.** Not a canvas
 * hash, not a font-metrics probe, not a WebGL renderer string, not any of the
 * things the phrase "device fingerprint" usually names. Those techniques work
 * by deriving an identifier the person cannot see, cannot clear, and did not
 * agree to — which is covert tracking, and §22 forbids exactly that class of
 * data leaving the system. This one is a UUID in `localStorage`: the person can
 * see it, clearing site data clears it, and a private window has its own.
 *
 * **The honest consequence, stated rather than hidden:** an abuser who clears
 * storage gets a new identity, so this is a weaker abuse signal than a covert
 * fingerprint would be. That trade is the right way round. ADR-0033 already
 * establishes that abuse detection **flags and cannot block**, so the cost of a
 * defeated signal is a human looking at one more report — and the cost of a
 * covert one is a civic product that tracks the citizens it exists to serve.
 *
 * Server-rendered surfaces never call this. It touches `localStorage`, so it
 * runs in the browser, and the value is read at the moment a draft is created
 * rather than during render — a fingerprint read during SSR would be a
 * different value on the server and the client, and hydration would flag it.
 */

const STORAGE_KEY = "nemesis.device";

/**
 * This browser's identifier, creating one on first use.
 *
 * Falls back to a per-session value when storage is unavailable — a private
 * window with storage blocked, an embedded webview with a locked-down profile.
 * The submission still works; the abuse signal is weaker for that session,
 * which is the same trade the paragraph above makes and is better than refusing
 * to accept a report.
 */
export function deviceFingerprint(): string {
  const ephemeral = sessionFallback();
  if (typeof window === "undefined") return ephemeral;

  try {
    const existing = window.localStorage.getItem(STORAGE_KEY);
    if (existing !== null && existing !== "") return existing;
    const minted = randomId();
    window.localStorage.setItem(STORAGE_KEY, minted);
    return minted;
  } catch {
    return ephemeral;
  }
}

let sessionValue: string | null = null;

function sessionFallback(): string {
  sessionValue ??= randomId();
  return sessionValue;
}

function randomId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `dev-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
}
