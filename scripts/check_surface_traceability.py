"""CI gate: §E27's event-to-surface table is true, complete, and current — M12.2.

    §6 Principle #9 requires every visual element to map to a real pipeline
    event. This table is the audit. A visual element not on this list, and not
    classifiable as chrome, is a defect.
        — NEMESIS-Frontend-Blueprint.md §E27

An audit table nobody executes is a list. This script executes it, in both
directions, against three sources that cannot be edited together by accident:

* **§E27 itself**, parsed from the frontend blueprint;
* **``backend/nemesis/events/catalog.py``**, read as an AST — the same technique
  and the same reason as ``check_event_catalog.py``: the api container mounts
  only ``backend/``, so a script that imported the application could not also
  read the blueprint;
* **``frontend/src/``**, read line by line for the event types it actually names.

Four findings are possible, and each is a different kind of lie:

1. **A row names an event that does not exist.** The visual has nothing behind
   it — the failure §6 Principle #9 is written against.
2. **A registered event is on no row.** The audit is incomplete, which is worse
   than absent, because §E28 says it about itself: *an honesty table that is
   wrong is worse than no table, because it is the artefact a reader trusts
   instead of checking.* Exempting one is a line in ``UNSURFACED`` below, with a
   reason, read in review — the discipline ``check-guards.ts`` uses.
3. **A row claims a live surface and nothing in ``src/`` binds the event.** The
   table says the browser reacts to something it has never heard of.
4. **A row is marked as waiting on a phase and the frontend binds it anyway.**
   The drift that flatters: a note saying *later* over a screen that already
   works. Caught because it is the direction nobody thinks to look in.

**How a row is driven is declared here, and that declaration is itself
checked.** It is a classification, not a second copy: the events and the
surfaces come from the blueprint and only the *mechanism* is stated here — and
stating it wrongly in either direction fails findings 3 or 4. A row is:

``stream``
    the browser reacts to the envelope. The event type must appear in
    ``frontend/src/`` outside ``generated/``.
``read``
    the surface renders the fact from an API read, and the event is the log's
    record of the same thing. The named route must exist under ``src/app/``.
``unbuilt``
    the surface is not built. The row must carry its phase in the visual cell —
    §E27's own ``*(Phase 14)*`` convention — and the event must **not** be bound.

Standard library only, host-side, no stack required. Exit code 0 clean, 1 on any
finding.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINT = ROOT / "NEMESIS-Frontend-Blueprint.md"
CATALOG = ROOT / "backend" / "nemesis" / "events" / "catalog.py"
WEB_SRC = ROOT / "frontend" / "src"

OK, FAIL = "[ OK ]", "[FAIL]"

#: Directories under `frontend/src/` whose contents are generated from a
#: contract rather than written. A generated client names every operation the
#: backend serves, so counting it as a binding would make finding 3 unfailable.
GENERATED = ("generated/",)

#: How each §E27 row's visual is driven, keyed by the row's first event.
#:
#: Every entry is checked. `stream` that is not bound and `unbuilt` that is
#: bound are both failures, so this table cannot drift quietly away from the
#: code — which is the only thing that makes declaring it here defensible.
DRIVEN: dict[str, str] = {
    # The pin arrives through the read that the envelope triggers, not through a
    # branch on the type: `clay/live.ts` deliberately refetches for every type it
    # does not draw, so that the map has one idea of what is true and not two.
    "complaint_submitted": "read",
    "exif_check_completed": "stream",
    "safety_trigger_fired": "stream",
    "classification_scored": "stream",
    "media_transcribed": "stream",
    "media_redacted": "stream",
    "perceptual_duplicate_detected": "stream",
    "cluster_match_found": "stream",
    "cluster_created": "stream",
    "cluster_merge_reverted": "stream",
    "severity_scored": "stream",
    "review_queued": "stream",
    "abuse_pattern_flagged": "stream",
    "pipeline_stage_degraded": "stream",
    # The studio reads revisions and their status; the events are the chain's
    # record of the same transitions, and rendering the list from the stream
    # would give one screen two sources for one fact.
    "policy_drafted": "read",
    "evaluation_set_published": "read",
    # **Both were `unbuilt` and both were wrong, and this gate is how.** The
    # rows carried a bare *(Phase 14)* / *(Phase 15)* note, which reads as
    # *nothing renders yet* — while `<EvidenceTrail>` has shown the assignment
    # row and the verification row to citizen, officer and public since M5. What
    # waits on those phases is the *console kanban* and the *printed SSIM score*,
    # not the fact. The rows now name both surfaces and scope the note to the
    # half it applies to.
    "work_order_created": "stream",
    "ssim_verification_completed": "stream",
    "citizen_confirmation_requested": "stream",
    "citizen_confirmed": "stream",
    "citizen_disputed": "stream",
    "taxonomy_published": "read",
    "admin_action": "stream",
    # Added at F18, and both are findings rather than descriptions — see
    # `docs/reports/e27-audit.md`. The events ship, they are the audit questions
    # *"which activations bypassed the evaluation set"* and *"who switched the
    # guardrail off"*, and no surface renders either. Marked `unbuilt` with the
    # phase that owns the screen, which is what §E27's convention is for.
    "evaluation_set_retired": "unbuilt",
}

#: Registered event types that are deliberately on no §E27 row, with the reason.
#:
#: Empty, and it should stay that way. It exists so that the answer to *"this
#: event has no visual"* is a reviewed line rather than a silent omission — and
#: so that the first person who needs one has to write down why.
UNSURFACED: dict[str, str] = {}

#: `frontend/src/` files whose match is prose rather than a binding. A line
#: beginning with a comment marker is already skipped; this covers the generated
#: honesty page, which quotes §E28's notes verbatim and therefore contains event
#: names as *text about* the product.
NOT_A_BINDING = ("public/generated/honesty.ts",)


def _fail(message: str) -> None:
    sys.stderr.write(f"  {FAIL} {message}\n")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def catalog_contents() -> tuple[set[str], dict[str, str]]:
    """``(registered, deferred)`` read from the catalog's AST."""
    tree = ast.parse(CATALOG.read_text(encoding="utf-8-sig"), filename=str(CATALOG))

    registered: set[str] = set()
    deferred: dict[str, str] = {}

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "register_event"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            registered.add(str(node.args[0].value))
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "DEFERRED_EVENT_TYPES"
            and isinstance(node.value, ast.Dict)
        ):
            for key, value in zip(node.value.keys, node.value.values, strict=True):
                if isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
                    deferred[str(key.value)] = str(value.value)

    return registered, deferred


class Row:
    """One §E27 row: the events it names, its surface, and its visual."""

    __slots__ = ("events", "line", "surface", "visual")

    def __init__(self, events: list[str], surface: str, visual: str, line: int) -> None:
        self.events = events
        self.surface = surface
        self.visual = visual
        self.line = line

    @property
    def key(self) -> str:
        return self.events[0]

    @property
    def phase_note(self) -> str | None:
        match = re.search(r"\*\(Phase (\d+)\)\*", self.visual)
        return match.group(1) if match else None


def e27_rows() -> list[Row]:
    """§E27's table, as rows. Raises if the section or its table is missing."""
    text = BLUEPRINT.read_text(encoding="utf-8-sig")
    start = text.find("## E27.")
    end = text.find("## E28.", start + 1)
    if start < 0 or end < 0:
        raise SystemExit(f"{FAIL} could not locate §E27 between its own heading and §E28")

    rows: list[Row] = []
    offset = text[:start].count("\n")
    for index, raw in enumerate(text[start:end].splitlines()):
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0] in {"Event", ""} or set(cells[0]) <= set("-: "):
            continue
        rows.append(
            Row(re.findall(r"`([a-z0-9_]+)`", cells[0]), cells[1], cells[2], offset + index + 1)
        )
    if not rows:
        raise SystemExit(f"{FAIL} §E27 was found and its table was not — the parser lost it")
    return rows


def bindings() -> dict[str, set[str]]:
    """Event type -> the `frontend/src/` files that name it in code."""
    registered, _ = catalog_contents()
    if not registered:
        raise SystemExit(f"{FAIL} the catalog parsed to zero registered event types")

    pattern = re.compile(r"\b(" + "|".join(sorted(registered, key=len, reverse=True)) + r")\b")
    found: dict[str, set[str]] = {}

    for path in sorted(WEB_SRC.rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        relative = path.relative_to(WEB_SRC).as_posix()
        if relative.startswith(GENERATED) or relative in NOT_A_BINDING:
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            # A comment naming an event is a sentence about the product, not a
            # binding. §E27 rows are quoted in the source all over this codebase
            # — deliberately — and counting those would make finding 3 vacuous.
            if raw.lstrip().startswith(("//", "*", "/*")):
                continue
            for event in set(pattern.findall(raw)):
                found.setdefault(event, set()).add(relative)

    return found


# ---------------------------------------------------------------------------
# The four findings
# ---------------------------------------------------------------------------


def check_rows_are_rows(rows: list[Row]) -> int:
    findings = 0
    for row in rows:
        if not row.events:
            _fail(f"§E27:{row.line} — a row that names no event: {row.surface!r}")
            findings += 1
        if not row.surface or not row.visual:
            missing = "surface" if not row.surface else "visual"
            _fail(f"§E27:{row.line} — {row.key} has no {missing}")
            findings += 1
    return findings


def check_every_event_exists(
    rows: list[Row], registered: set[str], deferred: dict[str, str]
) -> int:
    findings = 0
    for row in rows:
        for event in row.events:
            if event in registered or event in deferred:
                continue
            _fail(
                f"§E27:{row.line} — `{event}` is on the traceability table and is neither "
                f"registered in the catalog nor explicitly deferred. The visual has no event "
                f"behind it, which is the case §6 Principle #9 exists to forbid."
            )
            findings += 1
    return findings


def check_table_is_complete(rows: list[Row], registered: set[str]) -> int:
    listed = {event for row in rows for event in row.events}
    findings = 0
    for event in sorted(registered - listed):
        if event in UNSURFACED:
            continue
        _fail(
            f"`{event}` is registered and appears on no §E27 row. Either it reaches a surface "
            f"— in which case the audit table is missing it — or it deliberately reaches none, "
            f"in which case say so in UNSURFACED with the reason."
        )
        findings += 1
    for event in sorted(set(UNSURFACED) & listed):
        _fail(f"`{event}` is exempted in UNSURFACED and is on §E27 anyway — remove the exemption.")
        findings += 1
    return findings


def check_driving(rows: list[Row], bound: dict[str, set[str]]) -> int:
    findings = 0
    declared = set(DRIVEN)
    keys = {row.key for row in rows}

    for stale in sorted(declared - keys):
        _fail(f"DRIVEN names `{stale}`, which heads no §E27 row — the classification is stale.")
        findings += 1

    for row in rows:
        how = DRIVEN.get(row.key)
        if how is None:
            _fail(
                f"§E27:{row.line} — `{row.key}` is on the table and DRIVEN does not say how its "
                f"visual is driven. A row nobody has classified is a row nobody has audited."
            )
            findings += 1
            continue

        anywhere = {file for event in row.events for file in bound.get(event, set())}

        if how == "stream" and not anywhere:
            _fail(
                f"§E27:{row.line} — `{row.key}` is classified `stream` and nothing under "
                f"frontend/src/ names it. The table says the browser reacts to an event it has "
                f"never heard of."
            )
            findings += 1

        if how == "unbuilt":
            if row.phase_note is None:
                _fail(
                    f"§E27:{row.line} — `{row.key}` is classified `unbuilt` and its visual cell "
                    f"carries no *(Phase N)* note. An unbuilt surface that does not say so reads "
                    f"as a shipped one."
                )
                findings += 1
            if anywhere:
                _fail(
                    f"§E27:{row.line} — `{row.key}` is marked as waiting on a phase and "
                    f"{', '.join(sorted(anywhere))} binds it. A note saying 'later' over a screen "
                    f"that already works is the drift that flatters."
                )
                findings += 1

        if how == "read":
            route = ROOT / "frontend" / "src" / "app"
            if not route.is_dir():
                _fail(f"§E27:{row.line} — `{row.key}` is `read` and src/app/ does not exist")
                findings += 1

    return findings


def main() -> int:
    registered, deferred = catalog_contents()
    rows = e27_rows()
    bound = bindings()

    findings = 0
    findings += check_rows_are_rows(rows)
    findings += check_every_event_exists(rows, registered, deferred)
    findings += check_table_is_complete(rows, registered)
    findings += check_driving(rows, bound)

    if findings:
        sys.stderr.write(f"\nsurface traceability: {findings} finding(s)\n")
        return 1

    streamed = sum(1 for row in rows if DRIVEN[row.key] == "stream")
    unbuilt = sum(1 for row in rows if DRIVEN[row.key] == "unbuilt")
    types = len({event for row in rows for event in row.events})
    print(
        f"surface traceability: {len(rows)} §E27 rows over {types} event types — "
        f"{streamed} bound in the browser, {unbuilt} declared unbuilt with a phase, "
        f"{len(registered)} registered types all accounted for"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
