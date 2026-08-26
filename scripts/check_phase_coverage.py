"""Every ship line and every open register row is claimed — F1.

`docs/FRONTEND-EXECUTION-PLAN.md` owns *what and why*;
`docs/FRONTEND-PHASE-PLAN.md` owns *order and gate*. The split is only safe
because of the rule the second document opens with:

> **Every ship line in M7–M12, and every open row in the outstanding register
> (Group A and Group C), is claimed by exactly one phase below.** A line claimed
> by none is unplanned work; a line claimed by two is an ownership dispute.

Until this script existed that rule was a promise. A second planning artefact
with no such check is how two roadmaps end up describing different products,
which is the standing objection to the phase plan existing at all.

**What is asserted**

1. **Ship lines** — `M7.1`, `M8.13`, … enumerated in the execution plan's
   Group B — are each claimed by exactly one phase's `**Claims:**` line. Not
   zero, not two.
2. **Open register rows** — Group A and Group C — are each either claimed by a
   phase's `**Closes:**` line, or carry an explicit **disposition** in their own
   id cell. Three dispositions exist and each is a sentence somebody has to
   write down:

   * `Closed` / `Landed` / `Done` — the work happened.
   * `Accepted` — a recorded deviation that no phase will ever close, and
     should not read as debt (A12).
   * `Owned by …` — real work owned outside F1–F18, which Track E cannot claim
     on somebody else's behalf (C6, backend Phase 12).

   A row with neither a claim nor a disposition is the failure this catches:
   work that both documents assume the other one is tracking.
3. **A row claimed twice must say so.** Two phases may share a row only when
   each claim names its part — `A14 (citizen + public half)` and `A14
   (department half)`. A bare double claim is the ownership dispute the rule
   forbids.
4. **Nothing is claimed that does not exist.** A phase claiming `M8.99` or a
   register row that was deleted fails here rather than reading as coverage.

Standard library only, like every other host-side check in `scripts/`.

Usage::

    python scripts/check_phase_coverage.py
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXECUTION = ROOT / "docs" / "FRONTEND-EXECUTION-PLAN.md"
PHASES = ROOT / "docs" / "FRONTEND-PHASE-PLAN.md"

#: `- **M7.1** The console shell`
SHIP_LINE = re.compile(r"^-\s+\*\*(M(?:7|8|9|10|11|12)\.\d+)\*\*\s+(.+?)\s*$")

#: The id cell of a register row: `| A12 **Accepted** | …`, `| **C7** | …`,
#: `| ~~A3~~ **Closed** | …`.
REGISTER_ROW = re.compile(r"^\|\s*(?P<id>[~*\s]*(?P<key>[AC]\d+)[~*\s]*(?P<note>[^|]*?))\s*\|")

#: A disposition recorded in the id cell instead of a phase claim.
DISPOSITIONS = ("closed", "landed", "done", "accepted", "owned by")

#: `#### F1 · The safety rails`
PHASE_HEADING = re.compile(r"^####\s+(F\d+)\s")

#: A claim of a register row, with an optional part: `A14 (citizen + public half)`
CLOSES_ITEM = re.compile(r"(?:\*\*)?\b([AC]\d+)\b(?:\*\*)?\s*(\([^)]*\))?")


def read_ship_lines() -> dict[str, str]:
    lines: dict[str, str] = {}
    for line in EXECUTION.read_text(encoding="utf-8").splitlines():
        match = SHIP_LINE.match(line)
        if match is not None:
            lines[match.group(1)] = match.group(2)
    return lines


def read_register() -> dict[str, str | None]:
    """`{row id: disposition or None}` for every row in Groups A and C.

    Group B is the milestones and is covered by the ship lines above, so only
    the two tables that carry `A`/`C` ids are read here.
    """
    rows: dict[str, str | None] = {}
    for line in EXECUTION.read_text(encoding="utf-8").splitlines():
        match = REGISTER_ROW.match(line)
        if match is None:
            continue
        cell = match.group("id").lower()
        disposition = next((word for word in DISPOSITIONS if word in cell), None)
        # A row can appear twice — C5 and C6 are restated in the "Raised by M5,
        # owed by M6" table. First mention wins unless the second carries a
        # disposition the first did not, which is the direction drift travels.
        key = match.group("key")
        if key not in rows or (rows[key] is None and disposition is not None):
            rows[key] = disposition
    return rows


def read_phases() -> tuple[dict[str, list[str]], dict[str, list[tuple[str, str | None]]]]:
    """`{phase: [ship ids]}` and `{phase: [(register id, part)]}`."""
    claims: dict[str, list[str]] = defaultdict(list)
    closes: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    phase: str | None = None

    for line in PHASES.read_text(encoding="utf-8").splitlines():
        heading = PHASE_HEADING.match(line)
        if heading is not None:
            phase = heading.group(1)
            continue
        if phase is None:
            continue
        if line.startswith("**Claims:**"):
            claims[phase] += re.findall(r"M\d+\.\d+", line)
        if line.startswith("**Closes:**"):
            for key, part in CLOSES_ITEM.findall(line):
                closes[phase].append((key, part or None))
    return claims, closes


def main() -> int:
    ship_lines = read_ship_lines()
    register = read_register()
    claims, closes = read_phases()

    problems: list[str] = []

    if not ship_lines:
        problems.append(
            "no ship lines found in docs/FRONTEND-EXECUTION-PLAN.md — Group B's "
            "M7-M12 lists have lost their ids, and this check is now vacuous"
        )
    if not register:
        problems.append("no register rows found — the Group A and C tables have changed shape")

    # 1 and 4 — ship lines, claimed exactly once, and nothing invented.
    claimed_by: dict[str, list[str]] = defaultdict(list)
    for phase, ids in claims.items():
        for ship in ids:
            claimed_by[ship].append(phase)
            if ship not in ship_lines:
                problems.append(f"{phase} claims {ship}, which is not a ship line")

    for ship, description in sorted(ship_lines.items()):
        owners = claimed_by.get(ship, [])
        if not owners:
            problems.append(f"{ship} ({description}) is claimed by no phase — unplanned work")
        elif len(owners) > 1:
            problems.append(
                f"{ship} ({description}) is claimed by {', '.join(owners)} — an ownership dispute"
            )

    # 2, 3 and 4 — register rows.
    closed_by: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for phase, rows in closes.items():
        for key, part in rows:
            closed_by[key].append((phase, part))
            if key not in register:
                problems.append(f"{phase} closes {key}, which is not a register row")

    for key, disposition in sorted(register.items()):
        owners = closed_by.get(key, [])
        if disposition is not None:
            # A row somebody disposed of AND planned is not an error, but it is
            # worth saying out loud: one of the two statements is stale.
            if owners and disposition not in ("closed", "landed", "done"):
                problems.append(
                    f"{key} is recorded as '{disposition}' and is also claimed by "
                    f"{', '.join(phase for phase, _ in owners)} — one of the two is stale"
                )
            continue
        if not owners:
            problems.append(
                f"{key} is open, is claimed by no phase, and carries no disposition. "
                f"Either a phase closes it, or its row says why nothing will "
                f"(Accepted / Owned by ...)"
            )
        elif len(owners) > 1 and any(part is None for _, part in owners):
            problems.append(
                f"{key} is claimed by {', '.join(phase for phase, _ in owners)} and at least "
                f"one claim does not name its part — an ownership dispute"
            )

    for problem in problems:
        print(f"coverage: {problem}", file=sys.stderr)
    if problems:
        return 1

    open_rows = sum(1 for value in register.values() if value is None)
    print(
        f"coverage: {len(ship_lines)} ship lines and {open_rows} open register rows "
        f"({len(register)} total), each claimed by exactly one of "
        f"{len(set(claims) | set(closes))} phases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
