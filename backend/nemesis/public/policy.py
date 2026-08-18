"""What may appear on a public surface — declared, not filtered.

**Default deny, for the reason ADR-0016 gives.** The realtime envelope already
made this argument for the WebSocket stream: strip-the-sensitive-fields fails on
the next field somebody adds, because the new field is published by default and
nobody finds out until it is on a screen. Declare-what-is-allowed fails safe on
the same change.

Phase 4 needs the same rule with a wider blast radius. The realtime stream is
consumed by a map this system ships; §26.4 is consumed by anyone with curl, is
cached by intermediaries, and is archived by the people it is built for. A field
that leaks here leaks permanently.

So this module holds three things and nothing else:

1. **``PUBLIC_FIELDS``** — every field name any public response may carry, with
   the reason it is safe. A response model whose fields are not a subset fails a
   test, which is how the gate clause "every public field is provably free of
   exact GPS and citizen identifiers" is proven over the *schema* rather than
   over one sampled response body.
2. **``FORBIDDEN_VALUE_PATTERNS``** — a scan applied to rendered payloads, which
   catches what a field-name check structurally cannot: a coordinate at full
   precision hiding inside a field called ``centroid``, or a complaint UUID
   embedded in a string.
3. **``coarsen``**, re-exported from the realtime envelope rather than
   reimplemented. There is one definition of "coarse" in this system and both
   public surfaces use it; two definitions would drift and the drift would be
   invisible until somebody compared them.

**Why identifiers are excluded even though they are opaque.** A complaint UUID
reveals nothing by itself — and it is a stable handle to one citizen's report,
which is exactly what §26.4 means by "no citizen identifiers". Anyone who can
correlate one handle across two responses can rebuild a report history the
aggregates were supposed to have dissolved. Cluster and work-order ids are
different: they identify *an incident and the municipality's response to it*,
which is the thing §16.2's ward page exists to make public.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from nemesis.realtime.envelope import GPS_DECIMALS, coarsen

__all__ = ["GPS_DECIMALS", "PUBLIC_FIELDS", "coarsen", "find_disclosures"]


#: Every field name permitted anywhere in a public response body, with why.
#: A model declaring a field absent from this map fails ``test_public_privacy``.
PUBLIC_FIELDS: Final[dict[str, str]] = {
    # --- envelope ---------------------------------------------------------
    "api_version": "which contract produced this body",
    "generated_at": "when the aggregate was computed; a cached response is stale by design",
    "tenant": "the publishing organisation's slug — a public body, not a person",
    "notice": "the §22.2 disclaimer text carried with any system-flagged figure",
    "suppressed": "whether a bucket fell below the k-anonymity floor",
    "suppression_threshold": "the floor itself, published so a gap is explicable",
    # --- place ------------------------------------------------------------
    "zone_code": "a tenant-defined ward or site code — a place, not a person",
    "zone_name": "its display name",
    "zone_kind": "ward / campus block / estate, in the tenant's own vocabulary",
    "centroid": "a coarsened place centroid; see coarsen() and the value scan",
    "lat": "coarsened to ~110 m",
    "lng": "coarsened to ~110 m",
    # --- aggregate measures ----------------------------------------------
    "total_reports": "a count over a suppressed-below-k bucket",
    "open_reports": "as above",
    "resolved_reports": "as above",
    "auto_confirmed_resolutions": (
        "§44: an auto-confirmed closure stays distinguishable from a real one "
        "everywhere it surfaces, and a public resolution rate is one of those places"
    ),
    "resolution_rate": "derived from the two counts above",
    "median_resolution_hours": "a distribution statistic, never a per-report duration",
    "sla_breach_count": "a count",
    "sla_breach_rate": "derived",
    "by_category": "counts keyed by tenant taxonomy key — a defect type, not a reporter",
    "category": "a tenant taxonomy key",
    "count": "a count",
    "totals": "the v2 grouping object holding the counts above",
    "period": "the reporting window this row covers",
    "period_start": "window bound",
    "period_end": "window bound",
    # --- contractor track record (§16.1) ----------------------------------
    "contractor_id": (
        "a registered commercial entity, not a citizen; §16.1 makes its track "
        "record public and §16.4 gives it an appeal path in the same phase"
    ),
    "contractor_name": "as above — a company name is public record",
    "registration_id": "the public registration number the entity trades under",
    "active_since": "public registration date",
    "work_orders_completed": "a count",
    "work_orders_open": "a count",
    "on_time_rate": "derived",
    "disputed_count": "a count",
    "certified_categories": "taxonomy keys the entity is certified for",
    "rating_disclaimer": (
        "§16.1 forbids collapsing a contractor to a star rating and §22.2 requires "
        "any system-flagged figure to carry its 'unverified, under human review' text"
    ),
    # --- budget (§17.6) ---------------------------------------------------
    "fiscal_year": "a label, e.g. 2026-27",
    "funding_source": "a scheme or fund tag",
    "allocated_amount": "a public budget line",
    "spent_amount": "as above",
    "utilisation_rate": "derived",
    "currency": "ISO 4217 code",
    "allocations": "the list of the above",
    # --- bulk extract columns (public.export) -----------------------------
    # The extract is governed by the same allow-list as the aggregates, which is
    # the property the export module's docstring claims: a bulk download is the
    # most attractive way to exfiltrate this dataset, and "the export writes a
    # different serialiser" is how a scrub gets bypassed by accident.
    "reported_date": (
        "the day a report was made, never a timestamp — second resolution beside "
        "a coarse location re-identifies, because two people do not photograph the "
        "same corner in the same second"
    ),
    "created_date": "as above, for a work order",
    "sla_deadline_date": "the day a deadline falls; a municipal commitment, not a person",
    "closed_within_sla": "whether the commitment was met",
    "resolved": "whether the matter is finished",
    "status": "a lifecycle state from domain.lifecycle — platform structure, not tenant data",
    "severity_score": (
        "the §13.5 rubric output; a property of the defect, and §22.2's disclaimer "
        "travels with every aggregate that carries it"
    ),
    # --- collection wrappers ---------------------------------------------
    "items": "a list wrapper",
    "categories": "a list wrapper",
    "zones": "a list wrapper",
    "next_cursor": "opaque pagination position over aggregate rows",
    "count_suppressed_buckets": "how many buckets were withheld, so a gap is countable",
}


#: Field names that must never appear, with the disclosure each would be. The
#: allow-list above already excludes them; this exists so the *failure message*
#: names the harm rather than saying "unknown field", and so a reviewer adding a
#: field can see immediately whether they are re-adding a known mistake.
FORBIDDEN_FIELDS: Final[dict[str, str]] = {
    "complaint_id": "a stable handle to one citizen's report (§26.4)",
    "complaint_ids": "as above, in bulk — §26.3's own example gets this wrong",
    "merged_complaint_ids": "as above; the blueprint's example is knowingly not followed",
    "device_fingerprint": "§11.3 abuse signal; §22 forbids it leaving the system",
    "submitter_device_fingerprint": "the column form of the same value",
    "description_text": "the citizen's own words",
    "transcript": "the citizen's own words, transcribed",
    "photo_url": "media that has not been through the §22.1 blur promotion",
    "audio_url": "a recording of the citizen's voice",
    "audio": "as above",
    "exif_location": "the camera's exact position, which is the point of §11.1",
    "latitude": "use 'lat' inside a coarsened centroid; a bare full-precision pair is the leak",
    "longitude": "as above",
    "location": "the raw geography column",
    "reported_by": "a citizen identifier",
    "user_id": "a citizen or staff identifier",
    "subject": "an identity-provider subject claim",
    "email": "personal data",
    "phone": "personal data, including a contractor's — §17.1 keeps contact details internal",
    "registered_address": "§17.1 keeps entity-resolution inputs internal (defamation risk)",
    "director_names": "as above",
    "ip_address": "personal data under DPDP",
    "correlation_id": "internal tracing handle; correlates a caller's requests to each other",
    "key_digest": "an API key's stored digest",
    "secret": "any secret, by any name",
}


#: A coordinate with more precision than ``coarsen`` produces. Matched against
#: rendered JSON because the field-name check cannot see inside a nested object
#: that was built by hand instead of through the shaper.
#:
#: ``(?<![\d:.T-])`` is not decoration. The first version of this pattern matched
#: the fractional seconds of every ISO-8601 timestamp the API emits — ``…:45.123456``
#: reads as "a number with six decimal places" to a regex that only knows about
#: digits — so ``generated_at`` was reported as an exact GPS leak on every clean
#: response. A check that cries wolf on its own timestamp is one people switch
#: off, which would have cost the real coordinate finding it exists for.
_PRECISE_COORD = re.compile(rf"(?<![\d:.T-])-?\d{{1,3}}\.\d{{{GPS_DECIMALS + 1},}}")

#: Strings that are timestamps rather than data. Excluded outright as well as by
#: the lookbehind above, because a belt-and-braces exclusion here is cheaper than
#: another afternoon spent deciding whether a regex boundary holds for every
#: offset format.
_ISO_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")

#: A UUID in any string position. Public responses do carry two identifiers by
#: design — a contractor id and a zone code — so the scan is applied per field
#: with those exempted, rather than as a blanket ban that would be either
#: useless or wrong.
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

#: Fields whose value is legitimately a UUID. Everything else carrying one is a
#: leak, because the only UUIDs in this system are entity handles.
_UUID_PERMITTED: Final[frozenset[str]] = frozenset({"contractor_id"})


class Disclosure(str):
    """A finding. A ``str`` subclass so a failure prints as its own explanation."""

    __slots__ = ()


def find_disclosures(payload: Any, *, path: str = "") -> list[Disclosure]:
    """Every place a rendered public payload discloses something it must not.

    Total over dicts, lists, and scalars, and applied to the *serialised* body
    rather than to the model. A model is a promise about shape; this checks what
    was actually written, which is the only thing a consumer receives.

    Returns findings rather than raising, so a test can report all of them at
    once — one at a time turns a schema review into nine iterations.
    """
    findings: list[Disclosure] = []

    if isinstance(payload, Mapping):
        for key, value in payload.items():
            name = str(key)
            here = f"{path}.{name}" if path else name
            if name in FORBIDDEN_FIELDS:
                findings.append(Disclosure(f"{here}: forbidden field — {FORBIDDEN_FIELDS[name]}"))
            elif name not in PUBLIC_FIELDS and not _is_open_key_position(path):
                findings.append(
                    Disclosure(
                        f"{here}: field is not declared in PUBLIC_FIELDS. A public field "
                        f"exists because a shape declared it, never because a column was "
                        f"forwarded — add it there with the reason it is safe, or do not "
                        f"publish it"
                    )
                )
            if isinstance(value, str) and _UUID.search(value) and name not in _UUID_PERMITTED:
                findings.append(Disclosure(f"{here}: carries a UUID, which is an entity handle"))
            findings.extend(find_disclosures(value, path=here))

    elif isinstance(payload, str):
        if not _ISO_TIMESTAMP.match(payload) and _PRECISE_COORD.search(payload):
            findings.append(
                Disclosure(
                    f"{path}: a coordinate with more than {GPS_DECIMALS} decimal places; "
                    f"§22.1 treats an exact complaint location as personal data"
                )
            )

    elif isinstance(payload, float):
        if _is_coordinate_field(path) and round(payload, GPS_DECIMALS) != payload:
            findings.append(
                Disclosure(
                    f"{path}: {payload} carries more than {GPS_DECIMALS} decimal places; "
                    f"pass it through coarsen()"
                )
            )

    elif isinstance(payload, Sequence) and not isinstance(payload, str | bytes):
        for index, item in enumerate(payload):
            findings.extend(find_disclosures(item, path=f"{path}[{index}]"))

    return findings


def _is_coordinate_field(path: str) -> bool:
    return path.rsplit(".", 1)[-1] in {"lat", "lng"}


def _is_open_key_position(parent_path: str) -> bool:
    """Whether the *keys* at this level are data rather than field names.

    ``by_category`` is keyed by the tenant's own taxonomy keys, which are
    unbounded by construction — Phase 5 exists so a customer can invent
    ``co2_scrubber_fault`` without a code change, and an allow-list of field
    names cannot enumerate a vocabulary it is designed not to know. The *values*
    under those keys are still scanned; only the key names are exempt.
    """
    return parent_path.rsplit(".", 1)[-1].split("[")[0] in {"by_category"}


def clamp_suppression_threshold(tenant_value: int, floor: int) -> int:
    """The suppression floor actually applied to a tenant's aggregates.

    Clamped *up* rather than rejected. A tenant that has configured 1 has turned
    an aggregate endpoint into a per-complaint feed, which §26.4 forbids whoever
    asked for it — and failing the request instead would take a public
    transparency page offline over a configuration mistake, which serves nobody.
    Degrading toward more privacy is the direction that is safe to do silently.
    """
    return max(tenant_value, floor)
