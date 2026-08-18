# Secrets & rotation

Three rules, and everything else on this page follows from them:

1. **No secret in an image, a repository, or a log.**
2. **Every secret has a written rotation procedure, written before it is
   needed.** `nem parity` fails if a secret in the deployment contract is not
   documented here, so this is enforced rather than intended.
3. **A leaked secret is rotated, not reverted.** Removing the commit does not
   un-leak the value.

The list of secrets is not maintained by hand. It is derived from
`backend/nemesis/deployment.py`, which classifies every variable a deployed
environment must supply. Adding a secret there without adding a procedure here
fails CI — the mechanism that keeps this document from going stale the way
security documentation usually does.

## Where secrets live today

| Environment | Mechanism | Notes |
|---|---|---|
| Local | `.env`, git-ignored | Values are deliberately obvious and published in `.env.example`. They are not secrets and must not be treated as ones |
| CI | GitHub Actions secrets | Only `GITHUB_TOKEN` today |
| Deployed | **Not yet chosen** — Phase 1b | See below |

Phase 1b picks a secret manager alongside the deploy target, because the two
decisions are coupled: the manager, its IAM model, and the injection mechanism
are provider-shaped. What Phase 1a fixes is the part that is *not*
provider-shaped — the list of secrets, their blast radius, and the steps to
rotate each one. That work does not get thrown away when a provider is chosen.

## How secrets stay out of the three places they escape to

**Out of the repository.** `gitleaks` runs as a pre-commit hook and again as a
CI job over full history. The hook is the cheap moment; CI is the one that
cannot be bypassed with `--no-verify`. `.env` is git-ignored, `.env.example`
deliberately is not.

**Out of images.** Secrets arrive as runtime environment variables, never as
build args and never by copying a local `.env` into the build context. A build
arg is recorded in image metadata and a copied file survives in a layer even if
a later layer deletes it.

**Out of logs.** Every secret field on `Settings` is typed `SecretStr`, whose
`repr` redacts. A test asserts this holds for every secret in the contract, so
adding a plain `str` secret field fails the suite rather than leaking on the
first error that logs the settings object.

**Out of compose literals.** `nem parity` fails when a contract secret appears
in `docker-compose.yml` as a literal rather than a `${VAR:-default}`
substitution. A value that can only be changed by editing infrastructure is a
value that will never be rotated.

## Rotation

### General procedure

1. **Generate** the new value with a CSPRNG. Never reuse a value across
   environments — shared secrets turn one compromise into all of them.
2. **Stage** it. Where the system supports two valid values at once, add the new
   one before removing the old. Where it does not, accept the interruption
   deliberately rather than discovering it.
3. **Apply** and restart the services that read it.
4. **Verify** with a positive test — something that would fail if the old value
   were still in use. "Nothing broke" is not verification; it is also what
   happens when the rotation silently did not apply.
5. **Revoke** the old value.
6. **Record** it: date, who, why. A rotation with no record cannot be
   distinguished from one that never happened.

### `NEMESIS_JWT_SECRET`

**Blast radius: total authentication bypass.** This signs every session token.
The local value is published in this repository, so a deployment that kept it
has no authentication at all — anyone can mint a valid token. `app_env=pilot`
refuses to boot while it is still set, which is a guard and not a substitute for
rotation.

**Rotate on:** any suspected exposure, personnel change with production access,
and on a schedule once Phase 13 ships real identity.

```bash
python -c "import secrets;print(secrets.token_urlsafe(64))"
```

Set `NEMESIS_JWT_SECRET`, restart `api`. **Every outstanding token is invalidated
immediately** — that is the desired behaviour under compromise even though it
logs everybody out. Say so in the incident notes, so nobody "fixes" the logouts
by rolling back the rotation.

Verify: a token minted before the rotation must now be rejected. Overlapping
key support (`kid`-based rotation without a logout) is a Phase 13 item; until
then the interruption is the honest cost.

### `NEMESIS_CONTROL_PLANE_TOKEN`

**Blast radius: the meaning of every complaint, for every tenant.** This guards
tenant provisioning and every control-plane write — taxonomy, organisation,
zones, calendars, translations, contractor certifications. Holding it does not
let somebody read a citizen's report, and that is not the reassurance it sounds
like: it lets them *redefine what reports mean*. Deactivate a category and new
complaints stop being classifiable into it. Change a routing hint and work goes
to the wrong department. Move a holiday and every SLA deadline computed after
that point shifts, including the ones a contractor is measured against.

The local value is published in this repository, and `app_env=pilot` refuses to
boot while it is still set — a guard, not a substitute for rotation.

**This is a shared secret, not authentication**, and it is deliberately blunt:
there is no per-operator identity behind it until Phase 13, which is why every
control-plane mutation also writes an event to the tenant's hash chain. After a
suspected exposure, rotating is the first step and **reading that chain is the
second** — the token tells you nothing about who used it, and the events tell
you exactly what was changed and when.

**Rotate on:** any suspected exposure, any personnel change with operator
access, and immediately when Phase 13 replaces this mechanism.

```bash
python -c "import secrets;print(secrets.token_urlsafe(64))"
```

Set `NEMESIS_CONTROL_PLANE_TOKEN`, restart `api`. Nothing is invalidated except
the token itself — no sessions drop, and no tenant is affected — so unlike the
JWT rotation this one has no user-visible cost and no reason to delay.

Verify: a `POST /api/v1/control-plane/tenants` carrying the old token must
return 403, and one carrying the new token must return 201. Then audit what the
old token did:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc \
  "SELECT tenant_id, occurred_at, event_type, payload FROM events \
   WHERE entity_type = 'tenant' ORDER BY occurred_at DESC LIMIT 50"
```

### `NEMESIS_WEBHOOK_SIGNING_KEY`

**Blast radius: every webhook subscriber, simultaneously — and in both
directions.** Every endpoint's signing secret is
`HMAC(this key, endpoint_id || secret_version)`, so this single value is the
authenticity of every payload NEMESIS pushes to every partner.

The trade that produces that blast radius is deliberate and worth restating
here, because this is the page where somebody decides whether to rotate it.
Nothing signing-related is stored at rest: no ciphertext, no key-management
problem, no second secret, and a `pg_dump` leaked from a laptop contains no
signing material at all. The cost is that the one key cannot be rotated per
tenant.

**Exposure is worse than a leaked read credential.** A stolen read key lets
somebody see published aggregates. This one lets somebody *forge* a payload that
a tenant's handler verifies as genuinely ours — and a handler acts on what it
believes we sent. A forged `citizen_confirmed` or `work_order_created` is a
write into somebody else's system, with our signature on it.

The local value is published in this repository, and `app_env=pilot` refuses to
boot while it is still set.

**Rotate on:** any suspected exposure, and any personnel change with access to
deployment configuration. **Do not** rotate it on a schedule for its own sake —
see the coordination cost below.

```bash
python -c "import secrets;print(secrets.token_urlsafe(64))"
```

Set `NEMESIS_WEBHOOK_SIGNING_KEY` and restart `api` and `webhooks`.

**Every subscriber's verification breaks the moment this changes**, because every
derived secret changes with it. There is deliberately no overlap window at the
per-endpoint level and there is none here either — the reason to rotate is
usually that the value is believed compromised, and a rotation that leaves the
compromised value working for another hour has not rotated anything.

So the rotation is a **coordinated** one, and the sequence matters:

1. Notify every tenant with an active subscription, with a time.
2. Rotate the key and restart.
3. Re-issue each endpoint's secret so the tenant can read the new value:
   `POST /api/v1/integrations/webhooks/{id}/rotate-secret`. This is what makes
   step 4 possible — the secret is never retrievable, only re-derivable.
4. Each tenant deploys the new secret.

Between steps 2 and 4 deliveries **fail and retry** rather than being lost: the
schedule spans roughly ten hours, so a same-day coordination drains cleanly once
handlers are updated. A rotation that stretches past that window turns into
`failed` rows, and the gap has to be filled from the bulk export.

Verify — the fingerprint exists precisely so this check needs no secret:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc   "SELECT url, secret_version, secret_fingerprint FROM webhook_endpoints    WHERE is_active ORDER BY url"
```

Ask each tenant for the fingerprint their handler computes; it must match. Then
confirm deliveries are landing rather than accumulating:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc   "SELECT status, count(*) FROM webhook_deliveries GROUP BY status"
```

See `docs/runbooks/webhook-delivery-failing.md` for the failure this most often
produces: a tenant whose deploy has not landed, reporting 401s that look
identical to a clock-skew problem.

### `POSTGRES_PASSWORD`

**Blast radius: full read/write access to every complaint, event, and citizen
record.** Consumed by the postgres container and interpolated into
`NEMESIS_DATABASE_URL`; no application code names it, which is exactly why it is
the one most likely to be forgotten.

```bash
NEW=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
docker compose exec postgres psql -U nemesis -d nemesis \
  -c "ALTER ROLE nemesis WITH PASSWORD '$NEW';"
# then set POSTGRES_PASSWORD in .env and:
docker compose up -d api worker-io worker-ml beat
```

Order matters: change it in Postgres **first**, then in `.env`, then restart the
clients. Reversing that gives you a stack that cannot connect and a database
whose password you have not yet changed.

Verify: `docker compose exec api python -c "import asyncio..."` against the new
URL, or simply `curl localhost:8000/ready` — a wrong password surfaces as
`database: unavailable` within seconds.

**Note:** the `POSTGRES_PASSWORD` environment variable only *initialises* the
role on an empty data directory. On an existing volume it is read by clients but
does not change the role — which is why `ALTER ROLE` is the operative step and
editing `.env` alone silently does nothing.

### `GRAFANA_ADMIN_PASSWORD`

**Blast radius: read access to traffic patterns, error rates, and tenant
activity.** The observability stack is not exempt from secret handling because it
is "only monitoring" — dashboards disclose operational shape, and operational
shape is information about tenants.

```bash
docker compose exec grafana grafana cli admin reset-admin-password "$NEW"
```

Then set `GRAFANA_ADMIN_PASSWORD` in `.env` so a rebuild does not reintroduce the
old value.

Note that anonymous **Viewer** access is enabled on the local stack by design —
the alternative is a login prompt between an engineer and an alert they are
already looking at, which is how dashboards stop being opened. That trade is
local-only and must be revisited in Phase 1b, where the dashboards describe real
tenants rather than seed data.

### Datastore endpoint URLs

`NEMESIS_DATABASE_URL` and `NEMESIS_REDIS_URL` carry credentials inside the URL.
Treat the whole URL as a secret: it is easy to paste a "connection string" into a
ticket without registering that it contains a password.

## If a secret leaks

Go to [runbooks/credential-leak.md](runbooks/credential-leak.md). The short
version: rotate first, investigate second, and consider history rewriting last
and probably not at all.

## What Phase 1b adds

- A secret manager chosen with the deploy target, with per-environment values
  and an IAM model.
- Injection at runtime rather than through a file.
- Automated rotation where the mechanism supports it, and an alert on secret age
  where it does not.
- Overlapping-key JWT rotation (Phase 13), so signing-key rotation stops costing
  a logout.

None of that changes the list above. That is the point of writing it now.
