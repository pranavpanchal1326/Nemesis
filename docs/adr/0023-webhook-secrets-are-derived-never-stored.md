# 0023 — Webhook signing secrets are derived, never stored, and every target is re-validated at delivery

- **Status:** Accepted
- **Date:** 2026-08-17
- **Owner:** PLT · SEC
- **Blueprint:** §16.3, §25.1
- **Related:** ADR-0016 (realtime payloads are default-deny), ADR-0015 (outbox)

## Context

Phase 4 ships outbound webhooks with signed payloads. Two problems came with
them, and both are security problems wearing implementation clothes.

**A signing secret must be readable at delivery time.** Unlike an API key —
which is presented by a caller and can be compared against a one-way digest —
HMAC needs the plaintext every time we sign. The obvious options are storing it
in the clear, which puts a live credential in every backup and every `pg_dump` a
support engineer takes, or encrypting it, which introduces a key-management
problem: a second secret, a rotation procedure for *that* secret, and a
dependency, all to protect a value the deployment could recompute.

**A webhook URL is attacker-supplied by construction.** Anyone who can reach the
control plane can point one at `http://169.254.169.254/latest/meta-data/` or at
an admin interface on the deployment's own private network — and then read the
response status back out of the delivery log, which is tenant-inspectable by
design. That is a credential-exfiltration primitive with a delivery receipt.

## Decision

**The secret is `HMAC(deployment_key, endpoint_id || secret_version)`, and the
target's resolved address is checked before every delivery, not only at
registration.**

Signature format: `X-Nemesis-Signature: t=<unix>,v1=<hex>`, where the MAC covers
`"<timestamp>.<raw body>"`.

## Consequences

**Nothing signing-related is at rest.** A database dump reveals no signing
material. Rotation of one endpoint is an integer increment of `secret_version`,
which produces a value unrelated to the old one rather than derivable from it.

**The blast radius moves to one key.** `NEMESIS_WEBHOOK_SIGNING_KEY` is now the
authenticity of every payload to every subscriber, and rotating it invalidates
all of them at once. That is a real cost and it is stated in `docs/SECRETS.md`
with the coordinated rotation sequence, rather than left to be discovered during
the rotation.

**A `secret_fingerprint` column exists even though the secret is derivable.**
After a rotation, "is your handler on the current secret" is otherwise
unanswerable without one side transmitting the secret to find out.

**The timestamp is inside the signed string.** A signature over the body alone
is replayable forever: anyone who captures one delivery can resend it at any
future point and verification passes. Binding the timestamp into the MAC makes
it unforgeable and lets a receiver reject anything outside a tolerance window
(300 seconds, published).

**The scheme is versioned in the header.** When SHA-256 needs replacing, the
header carries both for a transition and receivers verify whichever they
support. A bare hex string would make that migration a flag day across every
tenant's infrastructure simultaneously.

**The SSRF guard resolves the hostname rather than matching the string.**
`http://spoof.example.com/` resolving to `127.0.0.1` is the whole attack, and a
regex over the URL cannot see it. Private, loopback, link-local, multicast,
reserved, and unspecified ranges are refused; so is plaintext `http`, and so are
inline credentials — the latter would land in a log the tenant reads back.

**Re-validating at delivery is what actually protects anything.** DNS answers
change between registration and delivery, and that gap *is* the rebinding
attack. A hostname that does not resolve at registration is therefore *accepted*
— refusing it would break the ordinary case of a tenant registering before their
DNS propagates — and checked again every time.

**Redirects are not followed.** A 302 to an internal address is the guard being
walked around one hop at a time, and re-running the check on every redirect
target is a more complicated route to the same place as refusing them.

**Delivered payloads go through the same default-deny shaper as the WebSocket
stream** (ADR-0016). A webhook is a *more* durable disclosure than a socket
frame — the receiver keeps it — so publishing more here than on the socket would
be exactly backwards. An event type with no declared public shape delivers an
empty payload.

**The response body is never recorded**, only the status code and the attempt
count. A receiver's body is attacker-influenced content landing on a surface the
tenant reads back through an API (§25), and the code is what diagnoses a
delivery failure anyway.

## Alternatives rejected

**Encrypting the secret at rest with a KMS or an application key.** Correct in a
deployment that already has key management; today it would mean building key
management to protect a recomputable value.

**A per-endpoint random secret stored in the clear.** Smaller blast radius per
endpoint, and it puts N live credentials into every backup instead of zero.

**An allow-list of subscriber domains.** Genuinely stronger, and it makes
self-service webhook registration impossible — which is the feature.

**An overlap window on rotation, accepting the old secret for an hour.**
Friendlier, and it defeats the purpose: the reason to rotate is usually that the
old secret is believed compromised, and a rotation that leaves the compromised
value valid has not rotated anything.
