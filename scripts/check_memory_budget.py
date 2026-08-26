"""The memory budget is arithmetic, so it is checked — ADR-0051.

`docs/HARDWARE.md` opens with *"RAM, not compute, is binding"* and then states a
budget: a table of per-service limits, two subtotals, and a claim that the total
fits the WSL2 VM with headroom. Every number in it was correct when it was
written and none of it was ever executed.

By F1 the table had drifted in the way a hand-maintained sum always drifts:
`relay` and `webhooks` were added to `docker-compose.yml` with 192 MB each and
never added to the table, so the documented total sat 384 MB below the declared
one. With the observability profile up, the declared total exceeded the VM — and
the symptom was `worker-ml`'s fork child being SIGKILLed at model load, which
reads as a model problem and is a spreadsheet problem.

This script makes the three claims checkable:

1. **Every `mem_limit` in `docker-compose.yml` appears in the table**, with the
   same number. A service added without a budget row fails here rather than in
   an OOM two months later.
2. **The subtotals are the sums they claim to be.**
3. **The totals fit the VM** — the application set with its stated headroom, and
   the application set plus observability within the VM at all.

Standard library only, like every other host-side check in this directory: it
has to run on a bare interpreter, including on a machine where the stack will
not start *because* of what this script is measuring.

Usage::

    python scripts/check_memory_budget.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yml"
HARDWARE = ROOT / "docs" / "HARDWARE.md"

#: Services started by `nem obs` rather than by `nem up`. Which services those
#: are is read from the compose file's own `profiles:` key, not listed here.
OBS_PROFILE = "obs"


def _mib(value: str) -> int:
    """`1536m` / `3g` -> mebibytes."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([mMgG])b?", value.strip())
    if match is None:
        raise SystemExit(f"memory: cannot read the limit {value!r}")
    number = float(match.group(1))
    return int(number * 1024) if match.group(2) in "gG" else int(number)


def read_compose() -> tuple[dict[str, int], set[str]]:
    """Return `{service: limit_mib}` and the set of services behind a profile.

    A hand-rolled scan rather than a YAML parse, for the reason every other
    check in this directory is standard-library only: this must run on an
    interpreter with nothing installed. The file's shape is regular enough that
    the scan is honest — two-space service keys, four-space service fields —
    and a file that stops being regular fails loudly below rather than quietly
    reporting a smaller total.
    """
    limits: dict[str, int] = {}
    profiled: set[str] = set()
    service: str | None = None
    in_services = False

    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if in_services and re.match(r"^\S", line):
            in_services = False
        if not in_services:
            continue

        key = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if key is not None:
            service = key.group(1)
            continue
        if service is None:
            continue

        limit = re.match(r"^    mem_limit:\s*(\S+)\s*$", line)
        if limit is not None:
            limits[service] = _mib(limit.group(1))
        profiles = re.match(r"^    profiles:\s*\[([^\]]*)\]\s*$", line)
        if profiles is not None and OBS_PROFILE in profiles.group(1):
            profiled.add(service)

    if not limits:
        raise SystemExit("memory: no mem_limit found — has docker-compose.yml changed shape?")
    return limits, profiled


def read_budget() -> tuple[dict[str, int], dict[str, int]]:
    """Return the documented per-service limits and the documented totals.

    Rows look like ``| `worker-ml` | 3072 MB | note |``; totals like
    ``| **Application subtotal** | **6528 MB** | note |``.
    """
    rows: dict[str, int] = {}
    totals: dict[str, int] = {}
    for line in HARDWARE.read_text(encoding="utf-8").splitlines():
        service = re.match(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*(\d+) MB\s*\|", line)
        if service is not None:
            rows[service.group(1)] = int(service.group(2))
            continue
        total = re.match(r"^\|\s*\*\*([^*|]+)\*\*\s*\|\s*\*\*(\d+) MB\*\*\s*\|", line)
        if total is not None:
            totals[total.group(1).strip().lower()] = int(total.group(2))
    return rows, totals


def read_vm() -> tuple[int, int]:
    """The usable VM size and the headroom the document requires, in MiB.

    Both are read from `docs/HARDWARE.md` rather than hardcoded, because the
    document is the contract — a machine with a different `.wslconfig` edits one
    file and this check follows it.
    """
    text = HARDWARE.read_text(encoding="utf-8")
    vm = re.search(r"<!--\s*budget:usable-vm-mb\s*=\s*(\d+)\s*-->", text)
    headroom = re.search(r"<!--\s*budget:min-headroom-mb\s*=\s*(\d+)\s*-->", text)
    if vm is None or headroom is None:
        raise SystemExit(
            "memory: docs/HARDWARE.md is missing its budget markers "
            "(<!-- budget:usable-vm-mb = N --> and <!-- budget:min-headroom-mb = N -->)"
        )
    return int(vm.group(1)), int(headroom.group(1))


def main() -> int:
    limits, profiled = read_compose()
    documented, totals = read_budget()
    usable_vm, min_headroom = read_vm()

    problems: list[str] = []

    # 1. The table covers the compose file, exactly.
    for service, limit in sorted(limits.items()):
        if service not in documented:
            problems.append(
                f"{service} declares mem_limit {limit} MB and has no row in the "
                f"docs/HARDWARE.md budget — this is the exact drift ADR-0051 records"
            )
        elif documented[service] != limit:
            problems.append(
                f"{service}: compose says {limit} MB, the budget says {documented[service]} MB"
            )
    for service in sorted(set(documented) - set(limits)):
        problems.append(f"{service} is budgeted for {documented[service]} MB and is not a service")

    application = sum(limit for service, limit in limits.items() if service not in profiled)
    observability = sum(limit for service, limit in limits.items() if service in profiled)

    # 2. The subtotals are sums.
    for label, actual in (
        ("application subtotal", application),
        ("observability subtotal", observability),
        ("wsl2 total, both profiles", application + observability),
    ):
        stated = totals.get(label)
        if stated is None:
            problems.append(f"docs/HARDWARE.md states no '{label}'")
        elif stated != actual:
            problems.append(f"{label}: the document says {stated} MB, the services sum to {actual}")

    # 3. The totals fit.
    #
    # Two different claims, deliberately. The application set is what `nem up`
    # starts and what a demo runs on, so it must fit *with* headroom — the
    # kernel, the page cache, and the burst above a limit that a cgroup allows
    # before it begins reclaiming. The observability profile is a debugging tool
    # a developer turns on knowingly, so it only has to fit at all.
    if usable_vm - application < min_headroom:
        problems.append(
            f"the application set declares {application} MB against a {usable_vm} MB VM, "
            f"leaving {usable_vm - application} MB — below the {min_headroom} MB this "
            f"budget requires"
        )
    if application + observability > usable_vm:
        problems.append(
            f"both profiles declare {application + observability} MB against a "
            f"{usable_vm} MB VM — over-subscribed by "
            f"{application + observability - usable_vm} MB. The first process the kernel "
            f"reaches for is worker-ml's fork child at model load, and it reads as a "
            f"model failure (ADR-0051)"
        )

    for problem in problems:
        print(f"memory: {problem}", file=sys.stderr)
    if problems:
        return 1

    print(
        f"memory: {len(limits)} services — {application} MB application, "
        f"{observability} MB observability, "
        f"{usable_vm - application - observability} MB headroom with both profiles up"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
