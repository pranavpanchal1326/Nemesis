# Pulling a kill switch

- **Severity:** procedure, not an alert
- **Owner:** SRE (mechanism) · the flag's declared owner (decision)
- **Alerts:** none — a kill switch is a *response*, not a condition

> The procedure for using the emergency handle, written down before it is needed
> and rehearsed as part of the Phase 1a gate. If you are reading this during an
> incident, the two things you need are on this page and take about thirty
> seconds: what the switch will do, and how to check it worked.

## Symptoms

You have decided a shipped capability should be turned off immediately, without
a deploy — because it is degrading, because it is failing intermittently at full
cost, or because you want to force a fallback path deliberately rather than wait
for it to be forced.

## How to confirm

List the declared flags and their resolved state. Kill switches are marked
separately so the relevant handles are not buried among rollout toggles:

```bash
nem flag list
```

Or, without a shell on the container:

```bash
curl -s localhost:8000/ops/flags | python -m json.tool
```

Read the flag's `description` before pulling it. Every flag is required to
describe **what turning it off actually does**, in operator language — that
requirement exists for this exact moment.

## Immediate mitigation

Pull the handle. `--actor` and `--reason` are mandatory for `kill` and for no
other operation: a kill switch entry with no attribution is a mystery during a
post-mortem, and the post-mortem is guaranteed.

```bash
nem flag kill <flag> --actor "$USER" --reason "incident <id>: <one line>"
```

Confirm it took effect. **A change takes up to one reload interval — five
seconds by default — not zero.** If you check instantly and see the old value,
wait and check again before concluding it failed:

```bash
sleep 6 && nem flag list
```

Restore when the underlying cause is fixed:

```bash
nem flag clear <flag> --actor "$USER" --reason "<cause> resolved"
```

**Do not leave a kill switch pulled.** A permanently killed flag is a capability
quietly removed from the product with no decision recorded anywhere. If it should
stay off, that is a change to the declared default, reviewed like any other
change — not an override left in Redis for six months.

## Root cause investigation

The kill switch is containment, never a fix. The incident that caused it still
needs its own runbook and its own post-mortem entry.

Two failure modes specific to the flag system itself:

- **`nem flag list` shows `default_store_unavailable` as the source.** Redis has
  never been reached by this process, so every flag is at its declared default
  and **your kill switch is not in effect**. See
  [redis-unavailable.md](redis-unavailable.md). This is the one case where
  pulling the handle does nothing and says nothing.
- **The flag is not declared.** The CLI rejects the name rather than creating it.
  That is intentional: a typo that silently created a new key would leave you
  believing you had disabled something.

## Prevention

- Every risky path ships behind a flag with a documented kill switch — an
  engineering standard applied per phase, not a retrofit.
- Every flag carries a `remove_by` date and CI fails once it passes. This is the
  only mechanism observed to actually remove flags; the alternative produces a
  codebase where every branch is conditional on a value nobody dares change.
- The Phase 1a gate requires a kill switch to be *exercised*, not merely to
  exist. An untested emergency handle is a guess.
