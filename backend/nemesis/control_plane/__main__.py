"""Operator CLI for the control plane.

    python -m nemesis.control_plane templates
    python -m nemesis.control_plane provision --file tenant.json
    python -m nemesis.control_plane show --tenant <uuid>

**Why this exists alongside the HTTP API.** The API is the product surface and
is what the Phase 5 gate exercises. This is the surface for the two cases the
API cannot serve: a deployment whose control-plane token has been lost or not
yet issued, and an operator who needs to see what a template *would* create
before a customer exists to create it against.

``sys.stdout`` is resolved per write rather than bound at import — the same
defect ``nemesis.flags.__main__`` documents, and Phase 0 hit before that with
``structlog``'s ``PrintLoggerFactory``. A stream replaced after import must not
silence the tool permanently.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from nemesis.control_plane import provisioning, taxonomy, templates
from nemesis.control_plane.errors import ControlPlaneError
from nemesis.control_plane.schemas import ProvisioningRequest
from nemesis.db.session import dispose_engine, session_scope
from nemesis.tenancy.context import tenant_scope


def _emit(line: str) -> None:
    sys.stdout.write(line + "\n")


def _list_templates() -> int:
    for template in templates.all_templates():
        _emit(
            f"{template.name:<18} v{template.version:<8} "
            f"{len(template.taxonomy):>3} categories  "
            f"{len(template.departments):>3} departments  "
            f"{len(template.zones):>3} zones"
        )
        _emit(f"{'':<18} {template.description}")
    return 0


async def _provision(path: Path) -> int:
    try:
        request = ProvisioningRequest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _emit(f"[FAIL] could not read {path}: {exc}")
        return 1

    try:
        async with session_scope() as session:
            result = await provisioning.provision(session, request=request)
    except ControlPlaneError as exc:
        _emit(f"[FAIL] {type(exc).__name__}: {exc}")
        return 1
    finally:
        await dispose_engine()

    _emit(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


async def _show(tenant_id: uuid.UUID) -> int:
    try:
        # Nested, not a single `async with ... , ...`: `tenant_scope` is a
        # synchronous context manager, and listing it alongside an async one
        # fails at runtime with a message about the asynchronous protocol
        # rather than at import.
        async with session_scope() as session:
            with tenant_scope(tenant_id):
                nodes = await taxonomy.list_nodes(
                    session, tenant_id=tenant_id, include_inactive=True
                )
                digest = await taxonomy.digest(session, tenant_id=tenant_id)
    finally:
        await dispose_engine()

    for node in nodes:
        marker = " " if node.is_active else "-"
        indent = "  " * node.depth
        _emit(f"{marker} {indent}{node.key:<28} {node.display_name}")
    _emit(f"\n{len(nodes)} node(s), content hash {digest.content_hash[:16]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nemesis.control_plane", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("templates", help="list the seeded tenant templates")

    provision = sub.add_parser("provision", help="create a tenant from a JSON request")
    provision.add_argument("--file", required=True, type=Path)

    show = sub.add_parser("show", help="print a tenant's taxonomy as a tree")
    show.add_argument("--tenant", required=True, type=uuid.UUID)

    args = parser.parse_args(argv)

    if args.command == "templates":
        return _list_templates()
    if args.command == "provision":
        return asyncio.run(_provision(args.file))
    return asyncio.run(_show(args.tenant))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
