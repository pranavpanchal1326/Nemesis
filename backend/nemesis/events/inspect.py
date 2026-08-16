"""Operator CLI for inspecting and verifying one entity's chain.

Exists because the alternative, at 2am, is an engineer writing ad-hoc SQL
against the event log — which is the single most common cause of the integrity
break this tool is used to investigate. Giving the responder a read-only command
that answers the question is cheaper than the incident their improvised query
causes.

Read-only, with no repair path and no write path. That is not a limitation, it
is the point: see ``docs/runbooks/event-chain-integrity.md``.

    python -m nemesis.events.inspect --tenant <UUID> --entity-type complaint \\
        --entity-id <UUID>
    python -m nemesis.events.inspect --tenant <UUID> --sweep
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from nemesis.db.session import dispose_engine, session_scope
from nemesis.events.verify import ChainVerification, sweep_chains, verify_chain
from nemesis.projections import replay_entity
from nemesis.tenancy.context import tenant_scope


def _emit(line: str = "") -> None:
    """Write to stdout, resolving the stream on **every** call.

    The same reasoning as ``nemesis.flags.__main__``: binding ``sys.stdout`` once
    at import is the defect Phase 0 hit with structlog's ``PrintLoggerFactory``
    and Phase 1a hit again in the flag CLI. A replaced stream — a test harness
    capturing output, a supervisor reattaching one — must not permanently
    silence an operator tool used during an incident.
    """
    sys.stdout.write(line + "\n")


def _report(verification: ChainVerification) -> None:
    header = f"{verification.entity_type} {verification.entity_id}"
    _emit()
    _emit(header)
    _emit("-" * len(header))
    _emit(f"  events checked : {verification.events_checked}")

    if verification.is_intact:
        _emit("  status         : INTACT")
        return

    _emit(f"  status         : BROKEN ({len(verification.breaks)} finding(s))")
    for found in verification.breaks:
        _emit(f"    sequence {found.sequence}: {found.kind} - {found.detail}")
    _emit()
    _emit("  Do not repair the chain. The break is evidence; a repaired chain is")
    _emit("  indistinguishable from a tampered one. See")
    _emit("  docs/runbooks/event-chain-integrity.md")


async def _verify_one(
    tenant_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID, *, show_state: bool
) -> int:
    # `tenant_scope` is a plain context manager, so it nests inside the async
    # one rather than joining it — mixing the two in a single `async with` list
    # fails at runtime with a confusing `__aenter__` error.
    async with session_scope() as session:
        with tenant_scope(tenant_id):
            verification = await verify_chain(
                session, tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id
            )
            _report(verification)

            if show_state:
                projected = await replay_entity(
                    session,
                    tenant_id=tenant_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    # Snapshots deliberately bypassed: an operator inspecting a
                    # suspect chain wants the state the *log* implies, not a
                    # cached answer that may predate what they are investigating.
                    use_snapshots=False,
                )
                _emit()
                _emit(f"  projected state (sequence {projected.sequence}):")
                for key, value in sorted(projected.state.items()):
                    _emit(f"    {key} = {value!r}")

    return 0 if verification.is_intact else 1


async def _sweep(tenant_id: uuid.UUID | None, limit: int) -> int:
    async with session_scope() as session:
        result = await sweep_chains(session, limit=limit, tenant_id=tenant_id)

    _emit(f"chains checked : {result.chains_checked}")
    _emit(f"chains broken  : {result.chains_broken}")
    for finding in result.findings:
        _report(finding)
    return 0 if result.chains_broken == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m nemesis.events.inspect",
        description="Verify and inspect event chains. Read-only.",
    )
    parser.add_argument("--tenant", type=uuid.UUID, help="tenant id (omit only with --sweep)")
    parser.add_argument("--entity-type", help="complaint | complaint_cluster | work_order | ...")
    parser.add_argument("--entity-id", type=uuid.UUID)
    parser.add_argument("--sweep", action="store_true", help="verify many chains instead of one")
    parser.add_argument("--limit", type=int, default=500, help="chains to check when sweeping")
    parser.add_argument(
        "--state", action="store_true", help="also print the projected state from the log"
    )
    args = parser.parse_args(argv)

    async def run() -> int:
        try:
            if args.sweep:
                return await _sweep(args.tenant, args.limit)
            if args.tenant is None or args.entity_type is None or args.entity_id is None:
                parser.error("--tenant, --entity-type and --entity-id are required without --sweep")
            return await _verify_one(
                args.tenant, args.entity_type, args.entity_id, show_state=args.state
            )
        finally:
            await dispose_engine()

    return asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover — operator entry point
    sys.exit(main())
