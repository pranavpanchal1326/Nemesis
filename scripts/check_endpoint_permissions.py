#!/usr/bin/env python3
"""CI gate: every endpoint declares a permission, or declares that it is public.

This is the Phase 13 exit-gate clause *"adding an endpoint without a declared
permission fails CI"*, executed. It is the same shape as
``check_tenant_scoping.py`` and for the same reason: standard library only, AST
only, no stack, no installed backend. A check that needs a database to tell you
the code is wrong gets skipped on the day it matters.

**The three rules.**

1. **Every route declares.** A route decorator must carry
   ``dependencies=[...]`` containing exactly one of ``requires("perm")``,
   ``requires_or_control_plane_token("perm")``, or ``public()``. The absence of
   a declaration is indistinguishable from forgetting one, which is why
   ``public()`` exists as a marker with no behaviour: being open becomes a
   decision somebody wrote down.

2. **The permission is real.** The string is checked against the registry in
   ``nemesis/identity/permissions.py``, read from *its* AST. A typo would
   otherwise be a role that silently permits nothing, discovered by an officer
   who cannot do their job.

3. **A scopable permission is narrowed in the handler.** ``requires`` only
   proves the caller holds the permission *somewhere* — ``api.identity_deps``
   explains the two-stage design. So a route whose declared permission has a
   resource kind must call ``authorize`` or ``visible`` in its body. Without
   this rule the gate clause about department A and department B would be
   unenforced: a handler could declare ``review:read`` and then return any row
   the caller named.

   Rule 3 does not apply to ``requires_or_control_plane_token``: that path can
   arrive with no principal at all (the deprecated Phase 5 shared secret), so
   there is nothing to narrow against. The dual path is itself the finding —
   ADR-0066 removes it — and it is counted and reported as a deprecation so the
   number cannot quietly grow.

Exit code 0 clean, 1 on any finding.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
API = BACKEND / "nemesis" / "api"
PERMISSIONS_MODULE = BACKEND / "nemesis" / "identity" / "permissions.py"

#: The HTTP verbs a router decorator can name. ``websocket`` is included: the
#: §26.3 socket is an endpoint that streams pipeline events, and an endpoint
#: that is exempt from this check by virtue of its transport is the gap
#: somebody would eventually move a read into.
ROUTE_DECORATORS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "websocket"}
)

#: The five ways a route may declare its posture. Each is a factory in
#: ``api.identity_deps`` and each means something different:
#: ``requires`` (a permission), ``requires_or_control_plane_token`` (the same,
#: plus the deprecated Phase 5 shared secret), ``authenticated`` (a session,
#: where the subject is the caller themself), ``machine`` (an API key with a
#: scope), ``public`` (deliberately open).
DECLARATION_CALLS = frozenset(
    {
        "requires",
        "requires_or_control_plane_token",
        "authenticated",
        "machine",
        "public",
    }
)

#: Declarations that carry no permission string, so rules 2 and 3 do not apply.
PERMISSIONLESS_CALLS = frozenset({"public", "authenticated", "machine"})

#: Calls in a handler body that count as narrowing. ``authorize`` answers "may
#: I touch this row"; ``visible`` answers "which rows exist for me". Both are
#: ``identity.decisions``; a handler that calls neither has not narrowed.
NARROWING_CALLS = frozenset({"authorize", "visible", "apply_complaint_visibility"})


@dataclass(frozen=True, slots=True)
class Finding:
    path: Path
    lineno: int
    message: str

    def render(self) -> str:
        return f"{self.path.relative_to(ROOT).as_posix()}:{self.lineno}: {self.message}"


def declared_permissions() -> dict[str, str | None]:
    """``{name: resource_kind}`` read from the registry's AST.

    Parsed rather than imported, for the reason the module docstring gives. The
    registry is a call to ``_declare`` with ``Permission(...)`` arguments, which
    is a shape an AST walk can read exactly — and that shape is load-bearing, so
    ``identity.permissions`` says so in its own docstring.
    """
    tree = ast.parse(PERMISSIONS_MODULE.read_text(encoding="utf-8-sig"))
    constants: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                constants[node.target.id] = node.value.value

    found: dict[str, str | None] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _callee(node) == "Permission"):
            continue
        name: str | None = None
        kind: str | None = None
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                name = str(keyword.value.value)
            if keyword.arg == "resource_kind":
                kind = _resolve_kind(keyword.value, constants)
        if name is not None:
            found[name] = kind
    return found


def _resolve_kind(node: ast.expr, constants: dict[str, str]) -> str | None:
    """``KIND_COMPLAINT`` -> ``"complaint"``; ``None`` -> ``None``."""
    if isinstance(node, ast.Constant):
        return None if node.value is None else str(node.value)
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _callee(node: ast.Call) -> str:
    """The function name a call names, ignoring the module path."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _is_route_decorator(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ROUTE_DECORATORS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id.endswith("router")
    )


def _declarations(decorator: ast.Call) -> list[tuple[str, str | None]]:
    """``[(call_name, permission_or_None)]`` from ``dependencies=[...]``."""
    found: list[tuple[str, str | None]] = []
    for keyword in decorator.keywords:
        if keyword.arg != "dependencies" or not isinstance(keyword.value, ast.List):
            continue
        for element in keyword.value.elts:
            # `Depends(requires("x"))` — unwrap one layer.
            inner = element
            if isinstance(inner, ast.Call) and _callee(inner) == "Depends" and inner.args:
                inner = inner.args[0]
            if not isinstance(inner, ast.Call):
                continue
            name = _callee(inner)
            if name not in DECLARATION_CALLS:
                continue
            permission: str | None = None
            if inner.args and isinstance(inner.args[0], ast.Constant):
                permission = str(inner.args[0].value)
            found.append((name, permission))
    return found


def _handler_narrows(handler: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call) and _callee(node) in NARROWING_CALLS
        for node in ast.walk(handler)
    )


def scan_file(path: Path, *, registry: dict[str, str | None]) -> tuple[list[Finding], int, int]:
    """``(findings, routes_seen, deprecated_dual_paths)``."""
    source = path.read_text(encoding="utf-8-sig")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover — lint fails first
        return [Finding(path, exc.lineno or 1, f"could not parse: {exc.msg}")], 0, 0

    findings: list[Finding] = []
    routes = 0
    deprecated = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        route_decorators = [
            decorator for decorator in node.decorator_list if _is_route_decorator(decorator)
        ]
        if not route_decorators:
            continue
        routes += len(route_decorators)

        for decorator in route_decorators:
            assert isinstance(decorator, ast.Call)
            declarations = _declarations(decorator)
            if not declarations:
                findings.append(
                    Finding(
                        path,
                        decorator.lineno,
                        f"route '{node.name}' declares no permission. Add "
                        f"dependencies=[Depends(requires('<permission>'))], or "
                        f"dependencies=[Depends(public())] if it is deliberately open",
                    )
                )
                continue
            if len(declarations) > 1:
                findings.append(
                    Finding(
                        path,
                        decorator.lineno,
                        f"route '{node.name}' declares {len(declarations)} permissions; "
                        f"one route, one declaration — two means a reader cannot tell "
                        f"which one is enforced",
                    )
                )
                continue

            call_name, permission = declarations[0]
            if call_name in PERMISSIONLESS_CALLS:
                continue
            if permission is None:
                findings.append(
                    Finding(
                        path,
                        decorator.lineno,
                        f"route '{node.name}' declares {call_name}() with no literal "
                        f"permission; this check reads the source, so a computed "
                        f"permission is one nobody can verify",
                    )
                )
                continue
            if permission not in registry:
                findings.append(
                    Finding(
                        path,
                        decorator.lineno,
                        f"route '{node.name}' declares unknown permission "
                        f"'{permission}'. Declared permissions live in "
                        f"nemesis/identity/permissions.py",
                    )
                )
                continue
            if call_name == "requires_or_control_plane_token":
                deprecated += 1
                continue
            if registry[permission] is not None and not _handler_narrows(node):
                findings.append(
                    Finding(
                        path,
                        node.lineno,
                        f"route '{node.name}' declares the scopable permission "
                        f"'{permission}' and never narrows it. requires() only proves "
                        f"the caller holds it *somewhere*; call decisions.authorize() "
                        f"with the loaded resource, or decisions.visible() for a list",
                    )
                )

    return findings, routes, deprecated


def main() -> int:
    registry = declared_permissions()
    if not registry:
        print(
            "[FAIL] endpoint permissions: read zero permissions from "
            f"{PERMISSIONS_MODULE.relative_to(ROOT).as_posix()} — the check would "
            "pass everything"
        )
        return 1

    findings: list[Finding] = []
    routes = 0
    deprecated = 0
    modules = 0
    for path in sorted(API.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        file_findings, file_routes, file_deprecated = scan_file(path, registry=registry)
        if file_routes:
            modules += 1
        findings.extend(file_findings)
        routes += file_routes
        deprecated += file_deprecated

    if not routes:
        print("[FAIL] endpoint permissions: found no routes at all — the scan is broken")
        return 1

    if findings:
        print(f"[FAIL] endpoint permissions: {len(findings)} finding(s) across {routes} route(s)")
        for finding in findings:
            print(f"  {finding.render()}")
        return 1

    note = (
        f", {deprecated} still accepting the deprecated control-plane token (ADR-0066)"
        if deprecated
        else ""
    )
    print(
        f"[ OK ] endpoint permissions: {routes} route(s) in {modules} module(s) declared "
        f"against {len(registry)} permission(s){note}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
