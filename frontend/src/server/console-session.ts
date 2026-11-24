import "server-only";

import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

/**
 * The operator session — ADR-0062, closing audit finding F5.
 *
 * **What this is for.** `/console` had no authentication of any kind: no
 * middleware, no session, no login. Worse than the missing gate was what the
 * BFF did in its absence — `controlPlaneHeaders()` attached
 * `X-Control-Plane-Token` to six privileged writes, so an anonymous visitor
 * could record the one permitted judgement on a citizen's report, activate a
 * policy, redefine a taxonomy, or change what a city publishes about itself.
 * The token was never stealable; its *authority* was being lent to the public.
 *
 * **What this is not.** Not an identity system. There are no users here —
 * Phase 13 owns that, and `HANDOVER.md` calls it the longest-lead blocker in
 * the repository. So the payload below carries issue and expiry times and
 * nothing else. There is deliberately no `user` field: a field named for
 * something that does not exist is a lie that outlives the ADR describing it,
 * and the first reader to trust it would be writing an audit trail against a
 * constant.
 *
 * Everybody who signs in is *an operator*. That is a real limitation, it is
 * written down in ADR-0062 under "what it does not close", and the honest
 * reading is that this is appropriate for a pilot with a small named team and
 * not for a deployment that must answer "who approved this".
 *
 * **Why the crypto is split from the configuration.** `signSession` and
 * `verifySession` take their key as an argument and touch no environment and no
 * cookie jar, so the security properties that matter — a tampered payload
 * fails, an expired token fails, a token signed with another key fails — are
 * testable without a request, without `process.env` mutation, and without the
 * Next runtime. Everything that reads the environment is below, and does no
 * cryptography of its own.
 */

/** The cookie's name. Not `session`: a shared name collides with whatever else
 *  a municipality's reverse proxy puts on the same host. */
export const SESSION_COOKIE = "nemesis_console";

/**
 * The signature scheme, carried **inside** the signed material.
 *
 * Bumped only when the payload's shape or the MAC changes. Inside the preimage
 * rather than beside it so a token minted under one scheme can never be
 * verified under another — the mismatch surfaces as a bad signature at the
 * exact transition, which is the same argument `events/hashing.py` makes for
 * `CHAIN_SCHEME_VERSION`.
 */
const SCHEME = "v1";

/** Twelve hours — one shift. Long enough that an officer is not challenged
 *  mid-shift, short enough that a walked-away terminal is not open tomorrow. */
const DEFAULT_SESSION_SECONDS = 43_200;

/** Tolerance for a clock that is behind the one that signed. Small on purpose:
 *  this is one process reading its own cookie, not two services federating. */
const CLOCK_SKEW_SECONDS = 30;

/** What a verified session is. Times are seconds since the epoch, as signed. */
export interface OperatorSession {
  readonly issuedAt: number;
  readonly expiresAt: number;
}

interface SessionPayload {
  readonly iat: number;
  readonly exp: number;
  /** Random per session, so two sessions minted in the same second are not the
   *  same token — which also means a cookie cannot be used as a timestamp
   *  oracle for when the configuration was last changed. */
  readonly jti: string;
}

function base64url(input: Buffer | string): string {
  return Buffer.from(input).toString("base64url");
}

function mac(secret: string, material: string): Buffer {
  return createHmac("sha256", secret).update(material).digest();
}

/** Mint a token. `now` is injectable so expiry is testable without waiting. */
export function signSession(
  secret: string,
  options: { readonly ttlSeconds: number; readonly now?: number },
): string {
  const now = options.now ?? Math.floor(Date.now() / 1000);
  const payload: SessionPayload = {
    iat: now,
    exp: now + options.ttlSeconds,
    jti: randomUUID(),
  };
  const body = `${SCHEME}.${base64url(JSON.stringify(payload))}`;
  return `${body}.${base64url(mac(secret, body))}`;
}

/**
 * Why verification returns `null` rather than throwing, and why every failure
 * returns the *same* `null`: a caller that could distinguish "the signature was
 * wrong" from "the payload did not parse" would be able to report which, and a
 * sign-in surface that reports which is an oracle. The reasons differ in the
 * server log and nowhere else.
 */
export function verifySession(
  secret: string,
  token: string | undefined,
  now: number = Math.floor(Date.now() / 1000),
): OperatorSession | null {
  if (token === undefined || token === "") return null;

  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [scheme, payloadPart, signaturePart] = parts;
  if (scheme !== SCHEME || payloadPart === undefined || signaturePart === undefined) return null;

  const expected = mac(secret, `${scheme}.${payloadPart}`);
  let supplied: Buffer;
  try {
    supplied = Buffer.from(signaturePart, "base64url");
  } catch {
    return null;
  }
  // Length-checked first because `timingSafeEqual` throws on a mismatch rather
  // than returning false, and a thrown error here would be a 500 on a bad
  // cookie — which is a denial of service anybody could trigger with a header.
  if (supplied.length !== expected.length) return null;
  if (!timingSafeEqual(supplied, expected)) return null;

  let payload: SessionPayload;
  try {
    payload = JSON.parse(Buffer.from(payloadPart, "base64url").toString("utf8")) as SessionPayload;
  } catch {
    return null;
  }
  if (typeof payload.iat !== "number" || typeof payload.exp !== "number") return null;
  if (payload.exp <= now) return null;
  // A token claiming to have been issued in the future is either a clock
  // problem or a forgery attempt against a rotated key. Neither is a session.
  if (payload.iat > now + CLOCK_SKEW_SECONDS) return null;

  return { issuedAt: payload.iat, expiresAt: payload.exp };
}

/**
 * Constant-time passphrase comparison.
 *
 * Both sides are hashed before comparison rather than compared directly,
 * because `timingSafeEqual` requires equal lengths — so comparing raw inputs
 * would either throw (leaking length by status) or need a length check first
 * (leaking length by timing). Hashing makes every comparison the same 32 bytes,
 * whatever was typed.
 */
export function passphraseMatches(expected: string, supplied: unknown): boolean {
  if (typeof supplied !== "string" || supplied === "") return false;
  const a = createHmac("sha256", "nemesis.console.passphrase.v1").update(expected).digest();
  const b = createHmac("sha256", "nemesis.console.passphrase.v1").update(supplied).digest();
  return timingSafeEqual(a, b);
}

/**
 * The configuration, or the precise reason there is none.
 *
 * **Fail closed, in every environment including local.** There is deliberately
 * no "open when `NODE_ENV` is not production" branch. That branch is exactly how
 * an unauthenticated console comes back: the developer path and the deployed
 * path stop being the same path, and the one nobody exercises is the one that is
 * wrong. `frontend/.env.example` carries both variables with local values, and
 * the repository already tells people to copy that file whole — `HANDOVER.md`
 * §5 documents this same mistake being made with `NEMESIS_CONTROL_PLANE_TOKEN`.
 */
export type ConsoleAuthConfig =
  | {
      readonly kind: "configured";
      readonly passphrase: string;
      readonly secret: string;
      readonly ttlSeconds: number;
    }
  | { readonly kind: "unconfigured"; readonly missing: readonly string[] };

export function consoleAuthConfig(): ConsoleAuthConfig {
  const passphrase = process.env["NEMESIS_CONSOLE_PASSPHRASE"];
  const secret = process.env["NEMESIS_CONSOLE_SECRET"];

  const missing: string[] = [];
  if (passphrase === undefined || passphrase === "") missing.push("NEMESIS_CONSOLE_PASSPHRASE");
  if (secret === undefined || secret === "") missing.push("NEMESIS_CONSOLE_SECRET");
  if (passphrase === undefined || passphrase === "" || secret === undefined || secret === "") {
    return { kind: "unconfigured", missing };
  }

  return { kind: "configured", passphrase, secret, ttlSeconds: sessionSeconds() };
}

function sessionSeconds(): number {
  const raw = process.env["NEMESIS_CONSOLE_SESSION_SECONDS"];
  if (raw === undefined || raw === "") return DEFAULT_SESSION_SECONDS;
  const parsed = Number.parseInt(raw, 10);
  // An unreadable value takes the default rather than zero. Zero would mint
  // sessions that are already expired, and the symptom — a sign-in that
  // succeeds and lands back on the sign-in page — reads as a cookie bug.
  return Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_SESSION_SECONDS;
}

/** The session on this request, or `null`. The single read every gate uses. */
export async function readOperatorSession(): Promise<OperatorSession | null> {
  const config = consoleAuthConfig();
  if (config.kind === "unconfigured") return null;
  const jar = await cookies();
  return verifySession(config.secret, jar.get(SESSION_COOKIE)?.value);
}

/**
 * Cookie attributes.
 *
 * `secure` follows `NODE_ENV`, and that is a statement about **transport**
 * rather than about the gate — a `Secure` cookie is not sent over plain `http`,
 * which would make local development impossible without weakening the gate
 * itself. The gate above has no environment branch; this flag does, and the
 * difference is deliberate.
 */
export function sessionCookieOptions(ttlSeconds: number): {
  readonly httpOnly: true;
  readonly sameSite: "strict";
  readonly secure: boolean;
  readonly path: "/";
  readonly maxAge: number;
} {
  return {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: ttlSeconds,
  };
}
