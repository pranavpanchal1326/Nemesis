# 0065 — A local password is scrypt from the standard library

- **Status:** Accepted
- **Date:** 2026-11-24
- **Owner:** SEC
- **Blueprint:** §18.4, §25; ADR-0063
- **Taken at:** Phase 13 (identity, authorization & org modelling)

## Context

Not every tenant has an identity provider. A municipality running a pilot has
eleven officers and a shared desktop; telling them to stand up OIDC before they
can triage a pothole is how a pilot does not happen. So the product needs a
local credential, and a local credential needs a password KDF.

The obvious answers are **argon2id** (`argon2-cffi`) and **bcrypt**. Both are
better known than what was chosen, and either would be defensible. Both are also
compiled wheels in the authentication path — which runs in the API image, both
worker images, and the bootstrap CLI — and ADR-0063 records why that placement
is the thing this repository argues about.

`hashlib.scrypt` is in the standard library, backed by OpenSSL, and is a
memory-hard KDF designed for this. Choosing it is **parameter selection, not
cryptography**: no primitive is implemented first-party, which keeps ADR-0063's
claim intact.

## Decision

**`hashlib.scrypt` at `n=2**15, r=8, p=1`, 32-byte output, 16-byte random salt,
with the parameters stored next to the digest.**

- ~32 MiB and ~50 ms per hash on the reference machine: the standard
  interactive-login figure, chosen deliberately *below* what the machine could
  bear, because sign-in is also the path a guessing attempt hits and we pay the
  cost on every attempt too.
- `maxmem` is computed from the parameters rather than left at its default,
  which otherwise rejects `n=2**15` outright with a confusing `ValueError` —
  the first thing anybody hits when raising these numbers.
- **`user_credentials.algorithm` and `.params` are columns.** Verification reads
  the parameters the digest was made with, so raising the cost later is a
  configuration change that applies credential-by-credential at next successful
  sign-in (`passwords.needs_rehash`). Module constants would mean the day the
  cost is raised is the day every existing password stops verifying.
- Input is **NFKC-normalised** before hashing. A passphrase typed with a
  composed "é" on one keyboard and a combining accent on another is one password
  to the person and two byte strings to scrypt; without this, a user who sets
  their password on Android cannot sign in from macOS, and the support call is
  unreproducible.
- The length floor counts **characters after normalisation**, not bytes. A byte
  floor would accept a shorter Latin password while rejecting a longer
  Devanagari one — the opposite of the intent, and invisible in an English-only
  test suite.

**What is deliberately absent:** complexity classes, and forced rotation.
`credential_policies.max_age_days` defaults to `0` — never expires — because
current guidance is that scheduled rotation drives people to `Password1!` and
`Password2!`. The two levers that do work are length and a breach-corpus check,
and the policy table carries both.

**Lockout is on the row, not in Redis.** A lockout that evaporates on restart is
a lockout an attacker clears with a deploy, and the restart is not hypothetical.
The cost is a write on a failed attempt, which is the path we are least
interested in making fast. The counter resets *with* the lock, so the next
lockout needs a fresh run of failures — a counter that kept climbing would let
one wrong guess after an expiry re-lock the account, which is a denial of
service against the legitimate user.

## Consequences

**A deployment that needs FIPS-validated or argon2id hashing has to change
this**, and the column-stored parameters are what make that migration
incremental rather than a flag day: add an algorithm, verify both, rehash on
sign-in.

**The unknown-user path costs a KDF.** `users._waste_time` runs one scrypt hash
when there is no credential to check, because otherwise an unknown address
answers in a millisecond and a known one in fifty — making "does this person
have an account here" measurable from anywhere. That is a deliberate waste of
~50 ms on the path we care least about.

**Sign-in is rate-limited per network at 60/hour with a burst of ten**, which is
not a property of the KDF but is the other half of the same control: lockout
bounds attempts against *one* account and does nothing against one attempt each
at ten thousand accounts. The number is chosen for a council behind one NAT
gateway rather than for a home connection.
