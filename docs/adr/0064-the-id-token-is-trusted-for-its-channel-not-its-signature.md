# 0064 — The ID token is trusted for its channel, not its signature

- **Status:** Accepted
- **Date:** 2026-11-24
- **Owner:** SEC
- **Blueprint:** §18.4, §25; ADR-0060, ADR-0063
- **Taken at:** Phase 13 (identity, authorization & org modelling)

## Context

Phase 13 ships "OIDC-capable identity … optional SSO for institutional
tenants". A university or a state IT department will not create local passwords
in a vendor's product, so this is the path that makes the product installable
rather than a nice-to-have.

An OIDC ID token is normally RS256. Verifying one in-process means an RSA
implementation, which means `cryptography` or `pyjwt` in the API image, the
worker images and the CLI — the dependency placement argument ADR-0063 records.
Implementing RSA ourselves is not on the table: ADR-0063's whole claim is that
no primitive is implemented first-party, and RSA signature verification is a
primitive with a long history of implementation-specific flaws (Bleichenbacher,
and every variant since).

So the question is not "library or no library". It is **whether the signature
needs verifying at all**, given where the token comes from.

OpenID Connect Core 1.0 §3.1.3.7 clause 6 answers it:

> If the ID Token is received via direct communication between the Client and
> the Token Endpoint (which it is in this flow), the TLS server validation MAY
> be used to validate the issuer in place of checking the token signature.

## Decision

**This implementation uses the authorization-code flow with PKCE, fetches the ID
token itself over TLS from the issuer's own token endpoint, and does not verify
its signature.** The trust basis is the channel.

What that requires, and all of it is implemented:

1. **The issuer must be `https`.** `oidc.register_provider` refuses anything
   else. Over plaintext there is no channel to trust, which would turn a citable
   decision into a vulnerability.
2. **The token endpoint is named by the issuer's own discovery document**, and
   the document must declare the issuer we configured. A mismatch is refused,
   because otherwise every later `iss` check is meaningless.
3. **The browser is never part of the trust path.** `oidc.complete` takes a
   `code`. There is no parameter, and no code path, that accepts an `id_token`
   — the request model is `extra="forbid"` and the test suite asserts the
   absence.
4. **Every claim is still checked**: `iss`, `aud`, `azp` when several audiences
   are listed, `nonce` against the value we stored when the flow started, `exp`
   and `iat` with the same thirty-second skew allowance as our own tokens.
5. **The email is used only if the provider says it is verified** and its domain
   is on the provider's allow-list. That last control is the one that stops the
   single most common OIDC misconfiguration there is: a tenant configures Google
   Workspace SSO and thereby admits every Google account on earth.
6. **State is single use** and PKCE is `S256`, never `plain` — a `plain`
   challenge *is* the verifier, which defeats the only thing PKCE is for.

## Consequences

**The implicit and hybrid flows are not supported and must never be added.**
They deliver an ID token through the browser, where the channel proves nothing
and the signature is the only thing between a forged token and a session. This
is the binding half of the decision: the function that decodes the token is
named `_unverified_claims`, so a reader who finds it called from anywhere other
than the token exchange has found a vulnerability and can see that from the name.

**A provider that rotates signing keys costs us nothing**, which is a genuine
benefit of this position rather than a consolation: there is no JWKS cache to go
stale and no key-rollover outage.

**We cannot support a provider that only issues ID tokens front-channel.** No
serious provider does; one that did would need this decision revisited along
with ADR-0063, not worked around.

**Discovery is cached on the provider row** with ADR-0060's rule applied: a
failed refresh with a cached document still works and records a degradation,
because a metadata endpoint being briefly slow must not make the customer's
sign-in our outage. With nothing cached there is nothing to degrade to, and that
is a 502 naming the provider.
