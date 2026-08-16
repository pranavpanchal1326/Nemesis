"""Operator CLI for feature flags.

    python -m nemesis.flags list
    python -m nemesis.flags kill  <flag> --actor <you> --reason "<why>"
    python -m nemesis.flags on    <flag> [--tenant T ...] [--rollout N]
    python -m nemesis.flags off   <flag> [--tenant T ...]
    python -m nemesis.flags clear <flag>

Usually reached through the task runner as ``nem flag <args>``.

WHY A CLI AND NOT AN ADMIN API ENDPOINT. An HTTP endpoint that mutates flags
needs authentication and authorisation, and those do not exist until Phase 13.
Shipping the endpoint now would mean shipping an unauthenticated write path
that can disable the safety fail-safe, and "we'll add auth later" is how that
becomes permanent. The CLI requires shell access to the container, which is a
real authorisation boundary today rather than a promised one. Read-only listing
*is* exposed over HTTP at ``/ops/flags``, because a flag's name and state are
not secrets and being able to see them without a shell is worth having. See
ADR-0009.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from nemesis.config import get_settings
from nemesis.flags import build_flags, get_spec
from nemesis.flags.registry import REGISTRY, UnknownFlagError
from nemesis.flags.store import FlagOverride


def _emit(line: str = "") -> None:
    """Write to stdout, resolving the stream on **every** call.

    Binding `sys.stdout` once at import is the same defect Phase 0 hit with
    `structlog`'s `PrintLoggerFactory`: the stream is captured at setup time, so
    anything that later replaces it — a process supervisor, a test harness, a
    reloader — breaks output permanently and silently. Resolving per call costs
    an attribute lookup and cannot go wrong.
    """
    sys.stdout.write(line + "\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nem flag", description="Feature flag operations.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Show every declared flag and its resolved state")

    for name, help_text in (
        ("on", "Turn a flag on (optionally for specific tenants, or a rollout %)"),
        ("off", "Turn a flag off (optionally for specific tenants)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("flag")
        p.add_argument("--tenant", action="append", default=[], help="Repeatable")
        p.add_argument("--actor", default="", help="Who is making this change")
        p.add_argument("--reason", default="", help="Why")
        if name == "on":
            p.add_argument("--rollout", type=int, default=None, help="Percent 0-100")

    p_kill = sub.add_parser("kill", help="Pull the emergency handle — off for everyone")
    p_kill.add_argument("flag")
    # Required here and nowhere else. A kill switch entry with no attribution is
    # a mystery during the post-mortem, and the post-mortem is guaranteed.
    p_kill.add_argument("--actor", required=True)
    p_kill.add_argument("--reason", required=True)

    p_clear = sub.add_parser("clear", help="Remove the override, restoring the declared default")
    p_clear.add_argument("flag")
    p_clear.add_argument("--actor", default="")
    p_clear.add_argument("--reason", default="")

    return parser


async def _run(args: argparse.Namespace) -> int:
    flags = build_flags(get_settings())

    if args.command == "list":
        decisions = await flags.snapshot()
        overrides = flags.overrides()
        width = max(len(n) for n in REGISTRY)
        _emit()
        for name, spec in sorted(REGISTRY.items()):
            decision = decisions[name]
            mark = "KILLED" if decision.source == "killed" else ("on" if decision.value else "off")
            tag = " [kill switch]" if spec.kill_switch else ""
            _emit(f"  {name.ljust(width)}  {mark:<6}  ({decision.source}){tag}")
            override = overrides.get(name)
            if override is not None:
                detail = []
                if override.tenants_on:
                    detail.append(f"on for {sorted(override.tenants_on)}")
                if override.tenants_off:
                    detail.append(f"off for {sorted(override.tenants_off)}")
                if override.rollout_percent is not None:
                    detail.append(f"rollout {override.rollout_percent}%")
                actor = override.actor or "unknown"
                detail.append(f"by {actor}: {override.reason or 'no reason given'}")
                _emit(f"  {' ' * width}  └─ {'; '.join(detail)}")
            _emit(f"  {' ' * width}     owner {spec.owner} · remove by {spec.remove_by}")
        _emit()
        return 0

    try:
        get_spec(args.flag)
    except UnknownFlagError as exc:
        _emit(f"error: {exc}")
        return 1

    if args.command == "kill":
        await flags.kill(args.flag, actor=args.actor, reason=args.reason)
        _emit(f"killed {args.flag} — off for everyone within one reload interval")
        return 0

    if args.command == "clear":
        await flags.clear(args.flag, actor=args.actor, reason=args.reason)
        _emit(f"cleared {args.flag} — back to its declared default")
        return 0

    tenants = frozenset(args.tenant)
    turning_on = args.command == "on"
    rollout = getattr(args, "rollout", None)

    if tenants:
        # Targeting a tenant set never changes the global value: "on for tenant
        # A" must not mean "on for everyone else too", which is the mistake this
        # branch exists to make impossible.
        override = FlagOverride(
            tenants_on=tenants if turning_on else frozenset(),
            tenants_off=frozenset() if turning_on else tenants,
            rollout_percent=rollout,
            actor=args.actor,
            reason=args.reason,
        )
        scope = f"for {sorted(tenants)}"
    else:
        override = FlagOverride(
            enabled=turning_on,
            rollout_percent=rollout,
            actor=args.actor,
            reason=args.reason,
        )
        scope = "globally"

    await flags.set_override(args.flag, override)
    _emit(f"{args.flag} {'on' if turning_on else 'off'} {scope}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
