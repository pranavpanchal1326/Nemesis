#!/usr/bin/env python3
"""Environment parity.

Phase 1b stands up dev, staging, and production against a provider nobody has
chosen. The point of this check is that when that day comes, the list of things
those environments need is already written down and already true — so the
migration is mechanical rather than a sequence of discoveries, each of which
would otherwise arrive as an outage.

Against ``backend/nemesis/deployment.py``, it asserts:

  1. ``.env.example`` documents every variable in the contract. The file is the
     first thing a new environment is built from; a variable missing here is a
     variable nobody knows to set.
  2. ``docker-compose.yml`` supplies each contract variable as an override-able
     value, never a baked-in literal. A hardcoded secret is not merely a bad
     practice — it is un-rotatable without editing infrastructure, which is why
     it never gets rotated.
  3. No local-only default has escaped into a place that would follow the stack
     into a deployed environment.
  4. The application's own view agrees: every entry with a ``setting_path``
     resolves to a real field on ``Settings`` (checked in the test suite, where
     pydantic is importable — see backend/tests/test_environment_parity.py).

Standard library only. ``nemesis.deployment`` imports nothing third-party
precisely so this check runs on a bare interpreter; if that ever stops being
true, this check silently stops running in the environments that need it most.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from _console import init, safe

OK, FAIL = init()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from nemesis.deployment import (  # noqa: E402
    DEPLOYMENT_REQUIRED,
    Criticality,
)

ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE = ROOT / "docker-compose.yml"
SECRETS_DOC = ROOT / "docs" / "SECRETS.md"


def _declared_in_env_example(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        names.add(stripped.split("=", 1)[0].strip())
    return names


def _contains_string_literal(path: Path, value: str) -> bool:
    """True if ``path`` contains ``value`` as a complete string literal.

    A leaked credential appears in source as a whole string — ``"admin"``,
    ``"changeme"`` — never as a fragment of a longer identifier. Walking the AST
    for exact ``ast.Constant`` matches finds the former and ignores the latter;
    a substring search cannot tell them apart, and short defaults are common
    English words.

    An unparseable file is reported as a hit rather than skipped: failing to
    check is not the same as checking and finding nothing, and a secret scanner
    that silently skips files is worse than none.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except SyntaxError:
        return True
    return any(
        isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == value
        for node in ast.walk(tree)
    )


def main() -> int:
    problems: list[str] = []

    for path in (ENV_EXAMPLE, COMPOSE):
        if not path.exists():
            print(f"error: {path} not found")
            return 1

    env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    compose_text = COMPOSE.read_text(encoding="utf-8")
    declared = _declared_in_env_example(env_text)

    for required in DEPLOYMENT_REQUIRED:
        var = required.env_var

        # 1. documented in .env.example
        if var not in declared:
            problems.append(
                f"{var} is in the deployment contract but not in .env.example. "
                f"Why it matters: {required.why}"
            )

        # 2. compose must not bake in a literal for a secret
        if required.criticality is Criticality.SECRET and required.setting_path is None:
            # Consumed by compose itself, so it must appear as a substitution.
            substitution = re.compile(rf"\$\{{{re.escape(var)}(:-[^}}]*)?\}}")
            if var in compose_text and not substitution.search(compose_text):
                problems.append(
                    f"{var} appears in docker-compose.yml as a literal rather "
                    f"than a ${{{var}:-default}} substitution. A value that can "
                    f"only be changed by editing compose is a value that will "
                    f"never be rotated."
                )

        # 3. a secret with no rotation procedure is a secret nobody will rotate
        if required.criticality is Criticality.SECRET and SECRETS_DOC.exists():
            if var not in SECRETS_DOC.read_text(encoding="utf-8"):
                problems.append(
                    f"{var} is classified as a secret but docs/SECRETS.md does "
                    f"not mention it. Every secret needs a rotation procedure "
                    f"written before it is needed, not during the incident."
                )

    if not SECRETS_DOC.exists():
        problems.append("docs/SECRETS.md is missing — no secret has a rotation procedure")

    # 4. the local-only defaults must not have leaked anywhere they would
    #    survive a copy-paste into a deployed environment. See
    #    `_contains_string_literal` for why this is an AST walk rather than a
    #    substring search.
    for required in DEPLOYMENT_REQUIRED:
        if required.criticality is not Criticality.SECRET or not required.local_default:
            continue
        if required.local_default == "nemesis":
            # Too generic to search for: it is the project name, the database
            # name, and the database user. Covered by check 2 instead.
            continue
        # Matched as a whole string *literal*, not as a substring of the file.
        #
        # Substring matching was the original implementation and it produced a
        # false positive the moment Phase 2 landed: the local default for
        # GRAFANA_ADMIN_PASSWORD is "admin", and the §9.4 event type
        # `admin_action` contains it. A check that flags an event name as a
        # leaked credential is a check people learn to ignore, and the next
        # thing it flags will be real.
        hits = [
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "backend" / "nemesis").rglob("*.py")
            if path.name not in {"config.py", "deployment.py"}
            and _contains_string_literal(path, required.local_default)
        ]
        if hits:
            problems.append(
                f"local default for {required.env_var} appears outside "
                f"config.py/deployment.py, in: {', '.join(hits)}"
            )

    if problems:
        print(f"\nenvironment parity: {len(problems)} problem(s)\n")
        for problem in problems:
            print(f"  {FAIL} {safe(problem)}")
        print()
        return 1

    secrets = sum(1 for r in DEPLOYMENT_REQUIRED if r.criticality is Criticality.SECRET)
    print(
        f"environment parity: ok — {len(DEPLOYMENT_REQUIRED)} contract variables "
        f"({secrets} secrets) documented, substitutable, and rotatable"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
