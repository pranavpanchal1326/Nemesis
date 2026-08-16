# Leaked credential

- **Severity:** critical
- **Owner:** SEC
- **Alerts:** none automated yet — `gitleaks` in CI and pre-commit is the detection

> A leaked credential in git history is a **rotation incident, not a revert**.
> Removing the commit does not un-leak the value: it was pushed, it was fetched,
> it is in someone's reflog and probably in a CI cache. Rewriting history is
> optional cleanup. Rotating the credential is not optional and comes first.
>
> Full rotation procedures per secret live in [../SECRETS.md](../SECRETS.md).
> This page covers the incident.

## Symptoms

- `gitleaks` fails in CI or blocks a commit.
- A secret appears in a log line, an error message, a screenshot, or a support
  ticket.
- A `.env` file was attached to something, committed, or pasted into a chat.

## How to confirm

```bash
docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:latest detect --source=/repo --redact
```

Establish three facts before doing anything, in this order:

1. **Which credential**, exactly — match it against the deployment contract in
   `backend/nemesis/deployment.py`, which classifies every secret.
2. **Was it ever pushed.** A pre-commit block that stopped it locally is a near
   miss with no blast radius. Treat it as a near miss, not an incident.
3. **What it grants.** `NEMESIS_JWT_SECRET` signs every session token: exposure
   means anyone can mint a valid token, which is total authentication bypass.
   `GRAFANA_ADMIN_PASSWORD` exposes traffic patterns and error rates. Different
   values, very different urgency.

## Immediate mitigation

1. **Rotate first.** Follow the per-secret procedure in
   [../SECRETS.md](../SECRETS.md). Do not investigate first — investigation
   takes hours and rotation takes minutes.

2. **Invalidate anything the old value issued.** For `NEMESIS_JWT_SECRET`,
   rotation invalidates every outstanding token by construction, which is the
   desired outcome even though it logs everybody out. Say so in the incident
   notes so nobody "fixes" the logouts by rolling back.

3. **Only then consider history.** If the value was pushed to a shared remote,
   rewriting history requires every clone to be re-cloned and does not recall
   what was already fetched. Usually the right call is to rotate, leave history
   alone, and record the decision.

4. **Open an incident record** even if the blast radius was nil. The near misses
   are where the systemic fix is visible.

## Root cause investigation

- **A `.env` that escaped `.gitignore`** — `.env` is ignored and `.env.example`
  is not, which is the correct arrangement and also a one-character mistake away
  from the wrong one.
- **A secret in a compose literal.** `nem parity` fails on this specifically:
  a value that can only be changed by editing compose is a value that will never
  be rotated.
- **A secret in a log line.** Every secret field on `Settings` is a
  `SecretStr`, whose `repr` redacts — and a test asserts it. If a secret reached
  a log, either that typing was bypassed or the value was interpolated into a
  string by hand.
- **A secret in an image layer.** Build args and `COPY` of a local `.env` both
  do this, and the value survives even if a later layer deletes the file.

## Prevention

- `gitleaks` runs as a pre-commit hook *and* as a CI job with full history —
  the pre-commit hook is the cheap moment to catch it, and CI is the one that
  cannot be bypassed with `--no-verify`.
- Every secret in the deployment contract must appear in `docs/SECRETS.md`;
  `nem parity` fails otherwise, so a new secret cannot ship without a rotation
  procedure written before it is needed.
- Phase 26's breach detection and notification runbook covers the case where the
  leaked credential guarded citizen data, which carries statutory obligations
  this page does not attempt to cover.
