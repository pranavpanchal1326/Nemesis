"""CI gate: no domain query may touch a tenant-scoped table unscoped.

Standard library only, so it runs without the stack and without installing the
backend — a check that needs a database to tell you the code is wrong will get
skipped on the day it matters.

**What this catches and what it does not.** This walks the AST, so it sees
statements written literally in source and nothing else. A predicate assembled
at runtime is invisible to it. That is not a weakness to apologise for, it is
why there are two layers: this one fails the build before merge, and
``nemesis.tenancy.guard`` fails the statement before execution. Raw ``text()``
SQL defeats both, so it is reported here as a finding requiring an explicit
waiver, and Postgres Row-Level Security in Phase 25 is the layer that also
binds a human with a psql session.

Exit code 0 clean, 1 on any finding.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

#: Packages whose queries must be tenant-scoped. Infrastructure packages
#: (config, observability, flags) do not touch domain tables at all.
#:
#: Every package that issues a query against a domain table belongs here. Phase 3
#: added four (`api`, `ingest`, `outbox`, `realtime`) and adding them to this
#: tuple was not optional bookkeeping: a domain package outside the list is not
#: "unchecked", it is *silently exempt*, and the static half of ADR-0014 would
#: have reported "clean" while the newest and least reviewed query code in the
#: system went unread.
#:
#: `policy` was missed when Phase 6 shipped and is added here by Phase 7 — the
#: exact failure the paragraph above describes, and it went unnoticed for a
#: release precisely because the check kept reporting "clean". Both packages
#: were in fact clean, which is the point: a check that would not have told us
#: otherwise is not evidence. `simulation` is listed from its first commit.
DOMAIN_PACKAGES = (
    "nemesis/api",
    "nemesis/db",
    "nemesis/events",
    "nemesis/ingest",
    "nemesis/outbox",
    "nemesis/pipeline",
    "nemesis/policy",
    "nemesis/projections",
    "nemesis/realtime",
    "nemesis/simulation",
    # Phase 8. Listed from its first commit, for the reason `policy` teaches by
    # having been missed: a package added to the codebase but not to this tuple
    # is a package this check reports "clean" for without reading.
    "nemesis/trust",
)

MODELS_PACKAGE = BACKEND / "nemesis" / "db" / "models"

#: The mixin that declares a model tenant-scoped. Reading the models' AST for it
#: keeps this script dependency-free *and* impossible to desynchronise from the
#: schema. The first version hardcoded the class names and needed a separate
#: test to prove the list was still accurate — which put the check's
#: trustworthiness in a second file that could itself be forgotten.
SCOPED_MIXIN = "TenantScopedMixin"

TENANT_COLUMN = "tenant_id"

#: `tenant_id` in a filtering position: an equality, an IN, or a bound
#: parameter comparison. Deliberately not `tenant_id` on its own — see
#: `_check_query` for why that distinction is the whole point of this check.
_PREDICATE_PATTERN = re.compile(
    r"tenant_id\s*(==|!=|\.in_\(|\.is_\()|"
    r"tenant_id\s*=\s*:|"
    r"tenant_id\s*=\s*[^=]"
)

#: A call that opts out must say so in a comment on the same line or the line
#: above. The marker is checked textually because an execution_options() call
#: can be several lines away from the select() it modifies.
EXEMPTION_MARKER = "tenant-scope-exempt:"


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    message: str

    def render(self) -> str:
        relative = self.path.relative_to(ROOT)
        return f"{relative}:{self.line}: {self.message}"


class QueryVisitor(ast.NodeVisitor):
    """Finds ``select(Model)`` / ``update(Model)`` / ``delete(Model)`` calls."""

    def __init__(self, path: Path, source_lines: list[str]) -> None:
        self.path = path
        self.lines = source_lines
        self.findings: list[Finding] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 — ast API
        name = _called_name(node.func)
        if name in {"select", "update", "delete"}:
            self._check_query(node, name)
        elif name == "text":
            self._check_raw_sql(node)
        self.generic_visit(node)

    def _check_query(self, node: ast.Call, kind: str) -> None:
        models = _referenced_models(node)
        if not models:
            return
        if self._is_exempt(node.lineno):
            return

        # The predicate may be attached anywhere in the enclosing expression
        # chain — .where(...), .filter(...) — so the whole statement is searched
        # rather than only this call's arguments.
        #
        # A *comparison* is required, not a mention. The first version of this
        # check accepted any occurrence of `tenant_id`, which meant
        # `select(EventChainHead.tenant_id, ...)` passed while returning every
        # tenant's rows — it selected the column into the result set without
        # constraining anything. That is the same false pass the runtime guard's
        # `_tables_with_tenant_predicate` avoids, and the two must agree or the
        # weaker one silently defines the policy.
        statement_source = _enclosing_source(self.lines, node.lineno)
        if _PREDICATE_PATTERN.search(statement_source):
            return

        self.findings.append(
            Finding(
                self.path,
                node.lineno,
                f"{kind}() over tenant-scoped {', '.join(sorted(models))} with no "
                f"{TENANT_COLUMN} predicate. Add one, or mark the line "
                f"'# {EXEMPTION_MARKER} <reason>' if it is legitimately cross-tenant.",
            )
        )

    def _check_raw_sql(self, node: ast.Call) -> None:
        if self._is_exempt(node.lineno):
            return
        sql = _static_string(node.args[0]) if node.args else None
        if sql is None:
            return
        sql = sql.lower()
        if not any(table in sql for table in _TABLE_NAMES):
            return
        if TENANT_COLUMN in sql:
            return
        self.findings.append(
            Finding(
                self.path,
                node.lineno,
                f"raw text() SQL over a tenant-scoped table with no {TENANT_COLUMN} "
                f"predicate. The runtime guard cannot inspect raw SQL, so this needs a "
                f"'# {EXEMPTION_MARKER} <reason>' comment or a scoped rewrite.",
            )
        )

    def _is_exempt(self, lineno: int) -> bool:
        window = self.lines[max(0, lineno - 3) : lineno + 2]
        return any(EXEMPTION_MARKER in line for line in window)


def discover_scoped_models() -> tuple[frozenset[str], frozenset[str]]:
    """``(class names, table names)`` for every tenant-scoped model.

    Read from ``nemesis/db/models/*.py`` by AST: a class is scoped if it lists
    ``TenantScopedMixin`` among its bases, and its table name is its
    ``__tablename__`` assignment.
    """
    classes: set[str] = set()
    tables: set[str] = set()

    for path in sorted(MODELS_PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {
                base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                for base in node.bases
            }
            if SCOPED_MIXIN not in base_names:
                continue
            classes.add(node.name)
            for statement in node.body:
                if (
                    isinstance(statement, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "__tablename__"
                        for target in statement.targets
                    )
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                ):
                    tables.add(statement.value.value)

    return frozenset(classes), frozenset(tables)


TENANT_SCOPED_TABLES, _TABLE_NAMES = discover_scoped_models()


def _static_string(node: ast.expr) -> str | None:
    """The statically-known text of a string expression, including f-strings.

    An f-string parses as ``JoinedStr``, not ``Constant``, so an earlier version
    of this check skipped every ``text(f"SELECT ... FROM {TABLE}")`` in the
    codebase — which is how most raw SQL is actually written. The interpolated
    parts are unknowable here; the literal parts are enough to recognise a table
    name and to spot a missing predicate.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = [
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        ]
        return "".join(parts) if parts else None
    return None


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _referenced_models(node: ast.Call) -> set[str]:
    found: set[str] = set()
    for argument in node.args:
        for child in ast.walk(argument):
            if isinstance(child, ast.Name) and child.id in TENANT_SCOPED_TABLES:
                found.add(child.id)
            elif isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
                if child.value.id in TENANT_SCOPED_TABLES:
                    found.add(child.value.id)
    return found


def _enclosing_source(lines: list[str], lineno: int) -> str:
    """Source of the statement containing ``lineno``.

    Walks outward while the expression is still open, so a select() whose
    .where() is fifteen lines further down is judged on the whole chain. Brace
    depth is a crude proxy for that, and crude is the right trade: the failure
    mode is a false *pass* on an exotically formatted query, which the runtime
    guard then catches anyway.
    """
    start = lineno - 1
    while start > 0 and lines[start - 1].rstrip().endswith(("(", ",", "\\")):
        start -= 1

    depth = 0
    collected: list[str] = []
    for index in range(start, len(lines)):
        line = lines[index]
        collected.append(line)
        depth += line.count("(") - line.count(")")
        if index >= lineno - 1 and depth <= 0:
            break
    return "\n".join(collected)


def scan_file(path: Path) -> list[Finding]:
    # utf-8-sig, not utf-8: a file written by a Windows editor carries a BOM,
    # which ast.parse rejects as a non-printable character. The finding it
    # produced was real but useless — it reported an encoding artefact where a
    # scoping violation might have been, on a check whose whole job is to be
    # trusted when it says "clean".
    source = path.read_text(encoding="utf-8-sig")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover — a syntax error fails lint first
        return [Finding(path, exc.lineno or 1, f"could not parse: {exc.msg}")]
    visitor = QueryVisitor(path, source.splitlines())
    visitor.visit(tree)
    return visitor.findings


def main() -> int:
    if not TENANT_SCOPED_TABLES:
        print(
            f"[FAIL] tenant scoping: discovered zero {SCOPED_MIXIN} models under "
            f"{MODELS_PACKAGE.relative_to(ROOT)} — the check would pass everything"
        )
        return 1

    findings: list[Finding] = []
    scanned = 0
    for package in DOMAIN_PACKAGES:
        directory = BACKEND / package
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.py")):
            scanned += 1
            findings.extend(scan_file(path))

    if findings:
        print(f"[FAIL] tenant scoping: {len(findings)} finding(s) in {scanned} module(s)")
        for finding in findings:
            print(f"  {finding.render()}")
        return 1

    print(
        f"[ OK ] tenant scoping: {scanned} domain module(s) clean against "
        f"{len(TENANT_SCOPED_TABLES)} tenant-scoped model(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
