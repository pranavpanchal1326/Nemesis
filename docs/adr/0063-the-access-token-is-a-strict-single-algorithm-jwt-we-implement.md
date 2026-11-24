# 0063 — The access token is a strict single-algorithm JWT we implement

- **Status:** Accepted
- **Date:** 2026-11-24
- **Owner:** SEC
- **Blueprint:** §8.2, §18.4, §25; ADR-0066
- **Taken at:** Phase 13 (identity, authorization & org modelling)

## Context

Phase 13 needs an access token. §18.4 names **FastAPI-Users + JWT**, and
`Settings.jwt_secret` / `jwt_algorithm` / `jwt_ttl_seconds` have existed since
Phase 0 waiting for a consumer.

Neither FastAPI-Users nor PyJWT is installed, and `pyproject.toml` already
carries a standing argument about what goes in which image: `imagehash` and
`exifread` were **removed** in Phase 8 because they would have landed in the ML
image and made the two §11.1 checks untestable in the image the test suite runs
in. Authentication is worse placed than either: it runs in the API image, in
both workers, and in the CLI that bootstraps the first operator.

The second consideration is the one that actually decided it. A general JWT
library's job is to support *every* algorithm, and every serious JWT
vulnerability of the last decade has come from that flexibility:

- `alg: none` — a token that asks not to be verified;
- HMAC-vs-RSA confusion — a token that asks to be verified with the public key
  as an HMAC secret;
- `kid` pointing at a key the attacker chose.

The mitigation, in a library, is a list of options that must be passed correctly
**on every call**. That is a control which depends on nobody ever writing
`jwt.decode(token, key)` without `algorithms=[...]` — and that call is the one
every tutorial shows.

## Decision

**`nemesis/identity/tokens.py` implements HMAC-SHA256 and nothing else.**

- The header is compared **as a whole** against the exact dict
  `{"alg": "HS256", "typ": "JWT"}`. There is no branch that reads an algorithm
  from a token and selects a verifier, so algorithm confusion has no code path
  rather than a defence.
- The algorithm comes from *our* configuration. `Settings.jwt_algorithm` is
  validated against the module's constant at startup; it does not select an
  implementation.
- Order of operations is structure → **signature** → claims. Every rejection
  below the signature check is therefore a rejection of a token we minted, so a
  forged token cannot take an observably different path from a stale one.
- Every claim is type-checked before use. A `sub` that is not a UUID is a 401,
  not a `ValueError` escaping a dependency as a 500. `exp: true` is rejected
  because `True` is an `int` in Python and would otherwise mean 1970.
- Refresh tokens are **not** JWTs. They are 256 bits from `secrets`, prefixed
  `nem_rt_` so secret scanners (including this repository's own `gitleaks` hook)
  match them, stored as an unsalted SHA-256 digest — the same reasoning
  `integrations.keys` records for API keys.

This is not hand-rolled cryptography: no primitive is implemented. What is
implemented is base64url, a JSON encoding with sorted keys, `hmac.new`, and
`hmac.compare_digest`.

## Consequences

**The obligation this creates, and it is binding: this module may never be
extended to verify an asymmetric signature.** The moment one is needed — a
provider's RS256 ID token — that is a different problem with a different answer,
and ADR-0064 is that answer. A pull request adding RSA here would quietly
reintroduce every vulnerability class the decision removes, so the ban is
written in the module docstring as well as here.

**We own the correctness.** `tests/test_identity_tokens.py` attempts each
historic attack class and asserts the rejection, including the ones that are
currently impossible — a test that cannot fail today is what catches the
refactor that makes it possible tomorrow.

**Determinism is a property we gained.** `json.dumps(..., sort_keys=True,
separators=(",", ":"))` means the same claims produce the same bytes, so a test
can assert on a token rather than on its decoding.

**What we gave up:** key rotation by `kid`, multiple audiences, and JWKS
publication. None is needed by a first-party console and a field app, and each
would be a reason to reach for a library — at which point this decision should
be revisited rather than worked around.
