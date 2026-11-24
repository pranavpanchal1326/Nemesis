import { createHmac } from "node:crypto";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  consoleAuthConfig,
  passphraseMatches,
  sessionCookieOptions,
  signSession,
  verifySession,
} from "@/server/console-session";

/**
 * ADR-0062 — the operator session that closes audit finding F5.
 *
 * `/console` had no authentication, and the consequence was not only an open
 * screen: `controlPlaneHeaders()` attached `X-Control-Plane-Token` to six
 * privileged writes, so an anonymous visitor could record the one permitted
 * judgement on a citizen's report or redefine a taxonomy.
 *
 * **What this file is for, and what it deliberately cannot test.** The gate
 * itself — a layout redirect and a refusal inside `controlPlaneHeaders()` —
 * needs a request, and it is asserted in `tests/console.spec.ts` against a real
 * browser. What lives here is the half that has no request: the token's
 * security properties. Each case below is a specific way a session could be
 * forged or outlive its welcome, written so that removing the corresponding
 * line in `console-session.ts` makes exactly one of them fail.
 *
 * A passing suite here plus a passing redirect there is the claim. Neither on
 * its own is.
 */

const SECRET = "test-secret-not-the-deployment-one";
const TTL = 3600;
const NOW = 1_800_000_000;

/**
 * `vi.stubEnv` rather than assigning `process.env` directly.
 *
 * `NODE_ENV` is typed read-only, so an assignment does not compile — and the
 * stub is the better tool anyway: `vi.unstubAllEnvs()` restores whatever the
 * runner started with, including variables this file never touched, so a test
 * cannot leak a passphrase into the next file's `consoleAuthConfig()`.
 */
afterEach(() => {
  vi.unstubAllEnvs();
});

describe("a session token proves it was issued by this deployment", () => {
  it("verifies one it just signed", () => {
    const token = signSession(SECRET, { ttlSeconds: TTL, now: NOW });
    const session = verifySession(SECRET, token, NOW + 1);

    expect(session).not.toBeNull();
    expect(session?.issuedAt).toBe(NOW);
    expect(session?.expiresAt).toBe(NOW + TTL);
  });

  it("refuses a token signed with a different key", () => {
    const token = signSession("someone-elses-secret", { ttlSeconds: TTL, now: NOW });

    expect(verifySession(SECRET, token, NOW + 1)).toBeNull();
  });

  it("refuses a tampered payload, which is the forgery that matters", () => {
    // Re-encode the payload with an expiry a century out and keep the original
    // signature — the exact move someone would make with a cookie editor.
    const parts = signSession(SECRET, { ttlSeconds: TTL, now: NOW }).split(".");
    const scheme = parts[0] ?? "";
    const signature = parts[2] ?? "";
    const forged = Buffer.from(
      JSON.stringify({ iat: NOW, exp: NOW + 3_153_600_000, jti: "forged" }),
    ).toString("base64url");

    expect(verifySession(SECRET, `${scheme}.${forged}.${signature}`, NOW + 1)).toBeNull();
  });

  it("refuses a token whose scheme has been changed", () => {
    const token = signSession(SECRET, { ttlSeconds: TTL, now: NOW });
    const swapped = token.replace(/^v1\./, "v2.");

    expect(verifySession(SECRET, swapped, NOW + 1)).toBeNull();
  });
});

describe("a session expires, and cannot claim to come from the future", () => {
  it("accepts one second before expiry and refuses one second after", () => {
    const token = signSession(SECRET, { ttlSeconds: TTL, now: NOW });

    expect(verifySession(SECRET, token, NOW + TTL - 1)).not.toBeNull();
    expect(verifySession(SECRET, token, NOW + TTL + 1)).toBeNull();
  });

  it("refuses a token issued beyond the clock-skew tolerance", () => {
    // Signed by a clock an hour ahead. Either a misconfigured host or a replay
    // against a rotated key; neither is a session worth honouring.
    const token = signSession(SECRET, { ttlSeconds: TTL, now: NOW + 3600 });

    expect(verifySession(SECRET, token, NOW)).toBeNull();
  });
});

describe("a malformed cookie is refused rather than thrown", () => {
  // Each of these is something a hostile or confused client can put in a
  // header. A throw here would be a 500 anybody could trigger at will, which
  // is a denial of service wearing the costume of a parse bug.
  it.each([
    ["undefined", undefined],
    ["empty", ""],
    ["no separators", "nonsense"],
    ["too few parts", "v1.abc"],
    ["too many parts", "v1.abc.def.ghi"],
    ["signature of the wrong length", "v1.abc.AAAA"],
    ["payload that is not JSON", `v1.${Buffer.from("not json").toString("base64url")}.AAAA`],
  ])("refuses a cookie that is %s", (_label, value) => {
    expect(() => verifySession(SECRET, value, NOW)).not.toThrow();
    expect(verifySession(SECRET, value, NOW)).toBeNull();
  });

  it("refuses a well-signed payload missing its times", () => {
    // Signed correctly, so this is not the signature check catching it — the
    // shape check is a separate guard and this proves it is load-bearing.
    const body = `v1.${Buffer.from(JSON.stringify({ jti: "x" })).toString("base64url")}`;
    const signature = createHmac("sha256", SECRET).update(body).digest().toString("base64url");

    expect(verifySession(SECRET, `${body}.${signature}`, NOW)).toBeNull();
  });
});

describe("the passphrase comparison", () => {
  it("accepts the configured passphrase and refuses a near miss", () => {
    expect(passphraseMatches("correct horse battery staple", "correct horse battery staple")).toBe(
      true,
    );
    expect(passphraseMatches("correct horse battery staple", "correct horse battery stapl")).toBe(
      false,
    );
  });

  it("refuses an empty or non-string attempt", () => {
    expect(passphraseMatches("anything", "")).toBe(false);
    expect(passphraseMatches("anything", undefined)).toBe(false);
    expect(passphraseMatches("anything", null)).toBe(false);
    expect(passphraseMatches("anything", 0)).toBe(false);
    // A form field that arrived as a File rather than a string, which is what
    // `formData().get()` returns for a file input of the same name.
    expect(passphraseMatches("anything", { toString: () => "anything" })).toBe(false);
  });
});

describe("the configuration fails closed", () => {
  it("is unconfigured when the passphrase is missing, and names it", () => {
    vi.stubEnv("NEMESIS_CONSOLE_PASSPHRASE", undefined);
    vi.stubEnv("NEMESIS_CONSOLE_SECRET", SECRET);

    const config = consoleAuthConfig();
    expect(config.kind).toBe("unconfigured");
    expect(config.kind === "unconfigured" && config.missing).toEqual([
      "NEMESIS_CONSOLE_PASSPHRASE",
    ]);
  });

  it("is unconfigured when the secret is missing, and names it", () => {
    vi.stubEnv("NEMESIS_CONSOLE_PASSPHRASE", "p");
    vi.stubEnv("NEMESIS_CONSOLE_SECRET", undefined);

    const config = consoleAuthConfig();
    expect(config.kind === "unconfigured" && config.missing).toEqual(["NEMESIS_CONSOLE_SECRET"]);
  });

  it("treats an empty string as missing, not as a configured empty secret", () => {
    // The failure this prevents: `NEMESIS_CONSOLE_SECRET=` in a .env file would
    // otherwise be a *valid* HMAC key that every deployment shares.
    vi.stubEnv("NEMESIS_CONSOLE_PASSPHRASE", "");
    vi.stubEnv("NEMESIS_CONSOLE_SECRET", "");

    const config = consoleAuthConfig();
    expect(config.kind).toBe("unconfigured");
    expect(config.kind === "unconfigured" && config.missing).toHaveLength(2);
  });

  it("falls back to the default lifetime rather than to zero", () => {
    // Zero would mint already-expired sessions, and the symptom — a sign-in
    // that succeeds and lands back on the sign-in page — reads as a cookie bug
    // rather than as a bad variable.
    vi.stubEnv("NEMESIS_CONSOLE_PASSPHRASE", "p");
    vi.stubEnv("NEMESIS_CONSOLE_SECRET", SECRET);
    vi.stubEnv("NEMESIS_CONSOLE_SESSION_SECONDS", "not a number");

    const config = consoleAuthConfig();
    expect(config.kind === "configured" && config.ttlSeconds).toBe(43_200);
  });
});

describe("the cookie cannot be read by script or sent cross-site", () => {
  it("is httpOnly, strictly same-site, and scoped to the whole app", () => {
    const options = sessionCookieOptions(TTL);

    expect(options.httpOnly).toBe(true);
    expect(options.sameSite).toBe("strict");
    expect(options.path).toBe("/");
    expect(options.maxAge).toBe(TTL);
  });

  it("is Secure in production", () => {
    vi.stubEnv("NODE_ENV", "production");
    expect(sessionCookieOptions(TTL).secure).toBe(true);
  });
});
