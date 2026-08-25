/**
 * The client-generated idempotency key — §E14, §26.1, M3, and M11's foundation.
 *
 * `nemesis/ingest/service.py` is explicit that §26.1 has **no natural key**:
 *
 * > two citizens photographing the same pothole from the same corner within a
 * > second are two genuine reports, not a duplicate submission — that is what
 * > §14's dedup is *for*. So a repeated submission is recognised only when the
 * > client says it is one.
 *
 * Which makes the key the client's job, and makes *when* it is minted the whole
 * property. **A key belongs to a submission, not to a request.** It is created
 * the moment a draft exists — when the shutter fires — and every attempt to
 * send that draft carries the same one: the first try, the retry after a
 * timeout, and the replay §E21's offline queue performs three hours later after
 * the app has been killed and reopened.
 *
 * Minting per request would produce exactly the failure the header exists to
 * prevent: a citizen on a bad connection taps send, the response is lost, they
 * tap again, and the city gets two reports of one pothole — which dedup will
 * then merge, telling them they are the second person to report their own
 * pothole.
 */

/**
 * A fresh key for one submission.
 *
 * `crypto.randomUUID` where it exists, which is everywhere this application
 * runs — it needs a secure context, and so does the camera §E17 opens with, so
 * a browser that lacks it cannot complete the flow anyway. The fallback exists
 * for the non-secure contexts a *test* runs in, and it says so rather than
 * pretending to the same guarantees.
 */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return fallbackKey();
}

/**
 * Not cryptographically strong, and not required to be.
 *
 * The key's job is uniqueness against *this client's own* other submissions, in
 * a namespace the server prefixes (`submit:`) so it cannot collide with the
 * pipeline's keys. It is not a secret and it is not a capability: guessing one
 * lets an attacker suppress a submission they already know the exact key of,
 * which is a strictly weaker position than simply not sending it.
 */
function fallbackKey(): string {
  const random = () =>
    Math.floor(Math.random() * 0x100000000)
      .toString(16)
      .padStart(8, "0");
  return `${random()}-${random()}-${random()}-${random()}`;
}
