import { cookies } from "next/headers";

import {
  consoleAuthConfig,
  passphraseMatches,
  SESSION_COOKIE,
  sessionCookieOptions,
  signSession,
} from "@/server/console-session";

/**
 * Sign in, and sign out — ADR-0062.
 *
 * The one route that turns the operator passphrase into a session. It is a
 * route handler rather than a server action for the same reason every
 * privileged write in this application is: the secret it compares against is
 * server-held, `server-only` makes a client bundle that reaches for it a build
 * error, and a form posting to a URL works with JavaScript disabled — which the
 * public doors already require of themselves (§E2) and which a sign-in page has
 * no excuse not to.
 *
 * **Every failure answers the same way.** Wrong passphrase, missing field,
 * unparseable body: `401` and one sentence. A sign-in surface that distinguishes
 * "no such passphrase" from "wrong passphrase" is an oracle, and there is only
 * one passphrase here, so there is nothing a specific message could usefully
 * tell an operator that it would not also tell everyone else.
 *
 * The exception is an **unconfigured** deployment, which answers `503` and names
 * the missing variables. That is not an oracle: it is a message to whoever
 * deployed this, the information is in `.env.example` already, and the
 * alternative — a `401` that no passphrase can ever satisfy — is the kind of
 * refusal that reads as a forgotten password and costs somebody an afternoon.
 * It is the same distinction `controlPlaneHeaders()` draws between a token that
 * is absent and one that is wrong.
 *
 * **No rate limiting, stated rather than overlooked.** The passphrase is
 * high-entropy by configuration, not by enforcement. A brute-force control
 * belongs with Phase 13's real authentication; bolting one onto a bridge would
 * need shared state this application does not have (ADR-0017's limiter lives
 * upstream, in the API). ADR-0062 records this under what it does not close.
 */

function problem(status: number, title: string, detail?: string): Response {
  return Response.json(
    { type: "about:blank", title, status, ...(detail === undefined ? {} : { detail }) },
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}

export async function POST(request: Request): Promise<Response> {
  const config = consoleAuthConfig();
  if (config.kind === "unconfigured") {
    return problem(
      503,
      "The console is not configured for sign-in.",
      `Set ${config.missing.join(" and ")} — see frontend/.env.example and ADR-0062.`,
    );
  }

  const supplied = await readPassphrase(request);
  if (!passphraseMatches(config.passphrase, supplied)) {
    return problem(401, "That passphrase was not accepted.");
  }

  const jar = await cookies();
  jar.set(
    SESSION_COOKIE,
    signSession(config.secret, { ttlSeconds: config.ttlSeconds }),
    sessionCookieOptions(config.ttlSeconds),
  );

  return Response.json({ signedIn: true }, { status: 200 });
}

/**
 * Sign out. Deletes the cookie and says it succeeded whether or not there was
 * one — "you are not signed in" is the same end state either way, and a 404 for
 * the no-session case would tell an unauthenticated caller whether somebody
 * else's cookie is live on this browser.
 */
export async function DELETE(): Promise<Response> {
  const jar = await cookies();
  jar.delete(SESSION_COOKIE);
  return Response.json({ signedIn: false }, { status: 200 });
}

/**
 * Both shapes, because the page posts a form and the e2e specs post JSON.
 *
 * A form post is what works without JavaScript; JSON is what a test and a
 * future scripted operator reach for. Accepting both costs four lines and
 * removes the temptation to make the page a client component just so it can
 * send a content type the server preferred.
 */
async function readPassphrase(request: Request): Promise<unknown> {
  const type = request.headers.get("content-type") ?? "";

  if (type.includes("application/json")) {
    try {
      const body: unknown = await request.json();
      if (typeof body !== "object" || body === null) return undefined;
      return (body as Record<string, unknown>)["passphrase"];
    } catch {
      return undefined;
    }
  }

  try {
    const form = await request.formData();
    return form.get("passphrase");
  } catch {
    return undefined;
  }
}
