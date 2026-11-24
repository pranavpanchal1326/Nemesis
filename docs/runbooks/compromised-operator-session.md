# Compromised operator session

- **Severity:** critical
- **Owner:** SEC
- **Alerts:** `NemesisRefreshTokenReuseDetected` — `nemesis_authentication_attempts_total{outcome="reuse_detected"}`

> **Revocation is immediate in this system, and that is the thing to use.** An
> access token is checked against its `user_sessions` row on every request
> (ADR-0066), so revoking a session or deactivating a user takes effect on the
> *next* request rather than when a token expires. You do not have to wait
> fifteen minutes, and you do not have to rotate `NEMESIS_JWT_SECRET` to end one
> person's access.
>
> Rotating the JWT secret signs **everybody** out of every tenant, including the
> field app mid-shift. It is the right move for a leaked signing key
> ([credential-leak.md](credential-leak.md)) and the wrong move for a stolen
> session.

## Symptoms

- `reuse_detected` on `nemesis_authentication_attempts_total`: a refresh token
  was presented twice. One of the two presenters is not the legitimate holder.
- A tenant reports activity they did not perform — a review decision, a role
  change, a policy activation they do not recognise.
- `authorization_decisions` shows a run of denials for one `user_id` across
  scopes that user has never worked in: somebody probing what a stolen session
  can reach.
- A session in `GET /api/v1/auth/sessions` with a `client_network` on another
  continent, or a `client_kind` that is not the console or the field app.

## How to confirm

Establish **which session**, **whose**, and **what it touched** — in that order,
and before revoking anything, because revoking first makes the first two
questions harder to answer.

```powershell
docker compose exec -T postgres psql -U nemesis -d nemesis -c "SELECT id, user_id, method, client_kind, client_network, issued_at, last_seen_at, revoked_at, revoked_reason, reuse_detected_at FROM user_sessions WHERE tenant_id = '<tenant-uuid>' ORDER BY issued_at DESC LIMIT 20;"
```

A non-null `reuse_detected_at` means the system already tore the family down —
see "Immediate mitigation": that part is done, and what is left is working out
what happened before it.

What the session did, from the tenant's own decision log:

```powershell
docker compose exec -T postgres psql -U nemesis -d nemesis -c "SELECT decided_at, permission, allowed, reason_code, resource_kind, resource_id, route FROM authorization_decisions WHERE tenant_id = '<tenant-uuid>' AND session_id = '<session-uuid>' ORDER BY decided_at;"
```

Remember what that table does and does not hold (`identity.permissions`): every
**denial**, and only the **allows** of permissions the registry marks `audited`.
An empty result for a reads-only session is expected and is not evidence that
nothing was read. For reads, the §25 answer is the HTTP access log plus
`last_seen_at`.

**Is it support rather than an attacker?** Check before escalating:

```powershell
docker compose exec -T postgres psql -U nemesis -d nemesis -c "SELECT id, operator_subject, justification, ticket_ref, allows_writes, started_at, ends_at, ended_at, decisions_allowed, decisions_denied FROM impersonation_sessions WHERE tenant_id = '<tenant-uuid>' ORDER BY started_at DESC LIMIT 10;"
```

An impersonated session has `method = 'impersonation'` and a non-null
`impersonation_id`. If a customer is asking "who read our data", this table is
the answer and the justification is in it.

## Immediate mitigation

**One session, when you know which:**

```powershell
docker compose exec -T postgres psql -U nemesis -d nemesis -c "UPDATE user_sessions SET revoked_at = now(), revoked_reason = 'revoked_by_admin' WHERE tenant_id = '<tenant-uuid>' AND id = '<session-uuid>' AND revoked_at IS NULL;"
```

**Everything that account holds**, which is the usual answer when a credential
rather than a token is suspected — and then take the password away, or the
attacker signs straight back in:

```powershell
docker compose exec -T postgres psql -U nemesis -d nemesis -c "UPDATE user_sessions SET revoked_at = now(), revoked_reason = 'revoked_by_admin' WHERE tenant_id = '<tenant-uuid>' AND user_id = '<user-uuid>' AND revoked_at IS NULL; UPDATE users SET is_active = false WHERE tenant_id = '<tenant-uuid>' AND id = '<user-uuid>'; DELETE FROM user_credentials WHERE tenant_id = '<tenant-uuid>' AND user_id = '<user-uuid>';"
```

Deactivation is reversible and deletion is not: the user row stays, its grants
stay for the access review, and reactivating is `is_active = true` plus a new
password. Do **not** delete the user — `authorization_decisions`,
`review_decisions` and every work order reference it.

**A support session the customer wants stopped**, as the customer:

```
DELETE /api/v1/access/impersonation/{id}
```

with a session holding `impersonation:read`. That revokes every session minted
under the impersonation, including ones created by a refresh.

**Only if the signing key itself is suspected**, rotate
`NEMESIS_JWT_SECRET` per [../SECRETS.md](../SECRETS.md) — and understand that
this ends every session in every tenant at once, which for the field app means
unsynced evidence sits in its offline queue (ADR-0056) until somebody signs in
again. It survives; it is not lost.

## Root cause investigation

Work outward from the session, not from the alert.

1. **Reuse detection fired — which half was the attacker?** The system cannot
   tell, which is why it revokes the whole family (`identity.sessions`). Look at
   `client_network` and `client_kind` on the family's rows: two distinct
   networks rotating the same lineage is theft; one network is more likely a
   client that retried a refresh whose response it never received.
2. **How was the credential obtained?** The candidates, in rough order of
   likelihood: a shared municipal desktop left signed in; a password reused from
   a breached service (the policy's `refuse_known_breached` lever exists for
   this and ships off); a token pasted into a support ticket or a screenshot; a
   browser extension on an officer's machine.
3. **What authority did it have?** `GET /api/v1/access/grants?user_id=...` or the
   `role_grants` table. A `tenant` scope means the blast radius was the whole
   tenant; a zone or department scope bounds it, and `authorization_decisions`
   will show the denials where it tried to leave that scope.
4. **Was anything changed?** Audited allows are in `authorization_decisions`;
   the durable record is the event log. Every mutation in this system is an
   event, so `SELECT * FROM events WHERE tenant_id = ... AND actor_id = ...`
   ordered by `recorded_at` is the authoritative answer, and it cannot have been
   edited.
5. **Was it support?** If so this is not an incident, it is a conversation — and
   the justification and ticket reference are in `impersonation_sessions`.

## Prevention

- **Do not issue tenant-wide grants by default.** The shipped
  `department_head` and `zone_officer` roles are meant to be granted *on a
  scope*; granting either tenant-wide removes the containment the Phase 13 gate
  is built around, and nothing stops you doing it.
- **Review `role_grants` on a cadence.** `GET /api/v1/access/grants` with
  `include_revoked=false` is the access review; delegations show
  `delegated_by_user_id` and an expiry, and a delegation that keeps being renewed
  is a grant somebody should issue properly.
- **Keep delegations short.** They are capped at ninety days
  (`identity.roles.MAX_DELEGATION_DAYS`) and the default should be the length of
  the leave, not the cap.
- **Watch `reuse_detected` rather than failed sign-ins.** A failed sign-in is
  somebody's typo; a reused refresh token is a credential in two places.
- **For a shared desktop, prefer shorter refresh windows per deployment** — the
  fortnight default is sized for the field app in a dead zone, not for a
  reception counter.
