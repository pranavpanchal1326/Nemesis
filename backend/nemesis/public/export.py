"""Bulk export for RTI applicants and researchers.

**Streamed, never assembled.** The obvious implementation builds a list, renders
it, and returns a string — which holds the whole extract in memory twice and
means a hundred-thousand-row export is a hundred-thousand-row allocation on a
process serving everything else. This yields rows as they arrive from a server-
side cursor, so the memory cost is one batch regardless of the extract size.

**CSV and NDJSON ship. Parquet does not, and this is the reason rather than an
omission.** The program plan says "CSV/Parquet". Parquet needs ``pyarrow``: a
40 MB dependency in the API image, for a columnar analytics format whose value
is column pruning and predicate pushdown against a query engine. The consumers
§16.3 names — RTI applicants, journalists, civil-society researchers — open
extracts in a spreadsheet or read them with a script, and both want CSV.
Phase 23 builds the analytics platform and is where a columnar extract belongs,
next to the warehouse that would make it worth having. ADR-0024 records the
decision so the gap is a choice on the record rather than something that fell
off the list.

**The export is the public projection, not the table.** Every row goes through
the same declared shapes ``public.policy`` governs, so an extract cannot carry a
field the API would refuse to serve. That property is load-bearing: a bulk
download is the single most attractive way to exfiltrate a dataset, and "the
export writes a different serialiser" is how the scrub gets bypassed by
accident.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from geoalchemy2 import Geometry
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.complaint import Complaint
from nemesis.db.models.work_order import WorkOrder
from nemesis.public.policy import coarsen
from nemesis.tenancy.context import tenant_scope

#: Rows fetched per round trip. Large enough that the per-batch overhead is
#: irrelevant, small enough that one export never holds a meaningful amount of
#: the process's memory.
STREAM_BATCH: Final = 1_000


@dataclass(frozen=True, slots=True)
class ExportFormat:
    name: str
    media_type: str
    extension: str


CSV_FORMAT: Final = ExportFormat("csv", "text/csv; charset=utf-8", "csv")
NDJSON_FORMAT: Final = ExportFormat("ndjson", "application/x-ndjson", "ndjson")

FORMATS: Final[dict[str, ExportFormat]] = {
    CSV_FORMAT.name: CSV_FORMAT,
    NDJSON_FORMAT.name: NDJSON_FORMAT,
}

#: The published column set for the complaints extract. **No complaint id.**
#:
#: An extract row is one citizen's report; giving it a stable handle would let
#: two extracts taken a month apart be joined into a per-reporter history, which
#: is the reconstruction the aggregates exist to prevent. What a researcher
#: actually needs — category, coarse place, time, and outcome — is all here, and
#: none of it identifies anybody.
COMPLAINT_COLUMNS: Final[tuple[str, ...]] = (
    "reported_date",
    "category",
    "zone_code",
    "lat",
    "lng",
    "status",
    "severity_score",
    "resolved",
)

WORK_ORDER_COLUMNS: Final[tuple[str, ...]] = (
    "created_date",
    "status",
    "zone_code",
    "sla_deadline_date",
    "closed_within_sla",
)


class UnknownFormatError(ValueError):
    """A format nobody declared."""


def resolve_format(name: str) -> ExportFormat:
    try:
        return FORMATS[name.lower()]
    except KeyError:
        raise UnknownFormatError(
            f"'{name}' is not an available export format; this API serves "
            f"{sorted(FORMATS)}. Parquet is deliberately not offered — see ADR-0024."
        ) from None


async def complaint_rows(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> AsyncIterator[dict[str, Any]]:
    """Stream the complaints extract, one scrubbed row at a time.

    ``reported_date`` is a **date, not a timestamp**. A second-resolution
    timestamp on a report with a coarse location is a re-identifier: two people
    do not photograph the same street corner in the same second, so the pair
    narrows to one person even though neither field does on its own. Truncating
    to the day is what makes the coarsening actually hold.
    """
    predicates = [Complaint.tenant_id == tenant_id]
    if since is not None:
        predicates.append(Complaint.reported_at >= since)
    if until is not None:
        predicates.append(Complaint.reported_at <= until)

    statement = (
        select(
            func.date(Complaint.reported_at).label("reported_date"),
            Complaint.category,
            Complaint.ward,
            func.ST_Y(cast(Complaint.location, Geometry)).label("lat"),
            func.ST_X(cast(Complaint.location, Geometry)).label("lng"),
            Complaint.status,
            Complaint.severity_score,
        )
        .where(*predicates)
        .order_by(Complaint.reported_at, Complaint.id)
        .limit(limit)
    )

    with tenant_scope(tenant_id):
        result = await session.stream(statement.execution_options(yield_per=STREAM_BATCH))
        async for row in result:
            yield {
                "reported_date": row.reported_date.isoformat(),
                "category": row.category,
                "zone_code": row.ward,
                "lat": coarsen(row.lat),
                "lng": coarsen(row.lng),
                "status": row.status,
                "severity_score": row.severity_score,
                "resolved": row.status in {"resolved", "closed"},
            }


async def work_order_rows(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> AsyncIterator[dict[str, Any]]:
    """Stream the work-order extract.

    No assignee, staff id, or contractor id. §16.1 publishes a contractor's
    aggregate track record through its own endpoint, where it arrives with the
    §22.2 disclaimer and the §16.4 appeal path attached. A per-job extract naming
    the contractor is the same accusation without either, delivered in a format
    designed for automated republication.
    """
    predicates = [WorkOrder.tenant_id == tenant_id]
    if since is not None:
        predicates.append(WorkOrder.created_at >= since)
    if until is not None:
        predicates.append(WorkOrder.created_at <= until)

    zone = (
        select(Complaint.ward)
        .where(
            Complaint.tenant_id == tenant_id,
            Complaint.cluster_id == WorkOrder.complaint_cluster_id,
        )
        .limit(1)
        .scalar_subquery()
        .label("zone_code")
    )

    statement = (
        select(
            func.date(WorkOrder.created_at).label("created_date"),
            WorkOrder.status,
            zone,
            func.date(WorkOrder.sla_deadline).label("sla_deadline_date"),
            WorkOrder.sla_deadline,
            WorkOrder.updated_at,
        )
        .where(*predicates)
        .order_by(WorkOrder.created_at, WorkOrder.id)
        .limit(limit)
    )

    with tenant_scope(tenant_id):
        result = await session.stream(statement.execution_options(yield_per=STREAM_BATCH))
        async for row in result:
            closed_within = (
                None
                if row.sla_deadline is None or row.status != "closed"
                else row.updated_at <= row.sla_deadline
            )
            yield {
                "created_date": row.created_date.isoformat(),
                "status": row.status,
                "zone_code": row.zone_code,
                "sla_deadline_date": (
                    None if row.sla_deadline_date is None else row.sla_deadline_date.isoformat()
                ),
                "closed_within_sla": closed_within,
            }


async def render(
    rows: AsyncIterator[dict[str, Any]],
    *,
    fmt: ExportFormat,
    columns: Sequence[str],
) -> AsyncIterator[bytes]:
    """Serialise a row stream into the requested format, incrementally."""
    if fmt is NDJSON_FORMAT:
        async for row in rows:
            yield json.dumps(row, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        return

    # csv.writer wants a file object; a fresh StringIO per row keeps the buffer
    # from growing to the size of the whole extract, which is the bug in every
    # "just write to a StringIO and return it" version of this function.
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="raise")
    writer.writeheader()
    yield buffer.getvalue().encode()

    async for row in rows:
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow(row)
        yield buffer.getvalue().encode()
