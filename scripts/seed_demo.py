"""Provision the local demo tenant — a city with geometry, two scripts, and a taxonomy.

**Why this exists.** Every gate script in `scripts/` proves a claim. This one
does not: it *creates the conditions* the citizen surfaces need in order to be
looked at, and it creates them the only way this repository allows — over HTTP,
through the Phase 5 control plane, with no code change and no SQL.

That constraint is the point rather than an inconvenience. §E17.1's *Place* card
resolves a coordinate to a ward, and until a tenant has ward **boundaries** the
card has nothing to say. The seeded tenants that exist today (the Phase 3, 4 and
5 gate runs) have zone names and no geometry, which is exactly the case
`nemesis/db/models/organisation.py` calls the common one at onboarding — so the
card's *"no boundaries configured"* branch is the one anybody running the app
locally has ever seen.

**The boundaries are approximate, and are labelled so in the data.** Each zone
carries `attributes.boundary_source = "approximate-demo"`. They are axis-aligned
boxes around real ward centroids, not PMC's shapefiles: this repository does not
ship third-party geodata it has not licensed, and §6 Principle #8's rule is that
a limitation is stated rather than dressed up. A tenant that has the real
shapefiles replaces these with one `PUT`, which is the whole argument for the
control plane.

Idempotent by slug: a second run reports the existing tenant rather than
provisioning a duplicate.

Usage::

    nem seed-demo                    # provision, print the tenant id
    nem seed-demo --slug my-city     # a different slug
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid
import urllib.error
import urllib.request
from typing import Any, Final

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _console import init  # noqa: E402

OK, FAIL = init()

BASE: Final = os.environ.get("NEMESIS_API_BASE", "http://localhost:8000")
CONTROL_PLANE: Final = f"{BASE}/api/v1/control-plane"
TOKEN_HEADER: Final = "X-Control-Plane-Token"
TENANT_HEADER: Final = "X-Tenant-ID"
DEFAULT_TOKEN: Final = "dev-only-insecure-control-plane-token-change-me"

#: Half-extent of a ward box, in degrees. ~1.3 km each way at this latitude.
#:
#: A box rather than a polygon traced off a map: a wrong polygon that *looks*
#: authoritative is worse than an obvious approximation, and the only property
#: §E17.1's card needs is that a coordinate in Kothrud resolves to Kothrud.
#:
#: The value is bounded by the closest pair of ward centroids below, and
#: `_check_no_overlap` asserts that rather than trusting this comment — a first
#: pass used 0.018 and silently produced two wards containing Shivajinagar, so
#: the endpoint's tie-break got exercised by a seeding bug instead of by the
#: nesting it exists for.
_HALF: Final = 0.012

#: A locality nests *inside* a ward, and Deccan Gymkhana genuinely does. Kept
#: deliberately, because a place tree with exactly one level below the city is a
#: place tree that never exercises the chain §E17.1's card renders.
_LOCALITY_HALF: Final = 0.004

#: (code, name, Marathi name, parent code, latitude, longitude, kind, half-extent)
WARDS: Final[tuple[tuple[str, str, str, str, float, float, str, float], ...]] = (
    ("W-KOTHRUD", "Kothrud", "कोथरूड", "Z-WEST", 18.5074, 73.8077, "ward", _HALF),
    ("W-AUNDH", "Aundh", "औंध", "Z-WEST", 18.5590, 73.8074, "ward", _HALF),
    ("W-WARJE", "Warje", "वारजे", "Z-WEST", 18.4820, 73.8020, "ward", _HALF),
    ("W-SHIVAJINAGAR", "Shivajinagar", "शिवाजीनगर", "Z-CENTRAL", 18.5308, 73.8478, "ward", _HALF),
    ("W-YERWADA", "Yerwada", "येरवडा", "Z-EAST", 18.5510, 73.8830, "ward", _HALF),
    ("W-HADAPSAR", "Hadapsar", "हडपसर", "Z-EAST", 18.5089, 73.9260, "ward", _HALF),
    ("W-KONDHWA", "Kondhwa", "कोंढवा", "Z-SOUTH", 18.4670, 73.8940, "ward", _HALF),
    ("W-KATRAJ", "Katraj", "कात्रज", "Z-SOUTH", 18.4480, 73.8570, "ward", _HALF),
    (
        "W-DECCAN",
        "Deccan Gymkhana",
        "डेक्कन जिमखाना",
        "W-SHIVAJINAGAR",
        18.5164,
        73.8416,
        "locality",
        _LOCALITY_HALF,
    ),
)

ZONES: Final[tuple[tuple[str, str, str], ...]] = (
    ("Z-WEST", "West Zone", "पश्चिम विभाग"),
    ("Z-CENTRAL", "Central Zone", "मध्य विभाग"),
    ("Z-EAST", "East Zone", "पूर्व विभाग"),
    ("Z-SOUTH", "South Zone", "दक्षिण विभाग"),
)

DEPARTMENTS: Final[list[dict[str, Any]]] = [
    {"code": "PWD", "name": "Public Works", "kind": "department"},
    {"code": "PWD-ROADS", "name": "Roads", "parent_code": "PWD", "kind": "division"},
    {"code": "PWD-DRAINS", "name": "Drainage", "parent_code": "PWD", "kind": "division"},
    {"code": "WATER", "name": "Water Supply", "kind": "department"},
    {"code": "ELEC", "name": "Street Lighting", "kind": "department"},
    {"code": "SWM", "name": "Solid Waste", "kind": "department"},
]

#: A taxonomy the platform has never seen, which is the Phase 5 claim. Keys are
#: immutable contracts (ADR-0019); the display names are translated below.
TAXONOMY: Final[list[dict[str, Any]]] = [
    {"key": "roads", "display_name": "Roads and footpaths", "is_selectable": False},
    {"key": "roads.pothole", "display_name": "Pothole", "parent_key": "roads"},
    {"key": "roads.footpath", "display_name": "Broken footpath", "parent_key": "roads"},
    {"key": "water", "display_name": "Water", "is_selectable": False},
    {"key": "water.leak", "display_name": "Pipeline leak", "parent_key": "water"},
    {"key": "water.drain", "display_name": "Blocked or open drain", "parent_key": "water"},
    {"key": "light", "display_name": "Street lighting", "is_selectable": False},
    {"key": "light.out", "display_name": "Street light out", "parent_key": "light"},
    {"key": "light.exposed", "display_name": "Exposed wiring", "parent_key": "light"},
    {"key": "waste", "display_name": "Waste", "is_selectable": False},
    {"key": "waste.dumping", "display_name": "Illegal dumping", "parent_key": "waste"},
    {"key": "waste.uncollected", "display_name": "Uncollected bin", "parent_key": "waste"},
]

#: What each selectable category *looks* and *reads* like — Phase 9's gate is
#: that "a new category is classifiable by adding prompts alone", and this is the
#: other half of the taxonomy above.
#:
#: **A tenant with a taxonomy and no prompt sets cannot classify anything.** The
#: classification stage abstains with *"this tenant has authored no prompts for
#: clip/en, text/en"*, §24.2 parks the report at `pending_classification`, and
#: the citizen surface's perception gate never settles. That is the pipeline
#: behaving correctly and a seeded tenant being incomplete — and it is why this
#: block exists rather than being left to a later `PUT`.
#:
#: Two encoders per node, deliberately. `clip` scores the photograph and `text`
#: scores the description or the transcript; a submission may carry either or
#: both (§26.1), and a node with prompts for only one of them silently cannot
#: score half the reports it is supposed to catch.
PROMPT_VERSION: Final = "pune-demo-1.0.0"

#: Positives and negatives per node. **The negatives are near-misses, not
#: generic non-defects**, and that distinction is the whole of Phase 9's prompt
#: craft rather than a nicety.
#:
#: `nemesis/perception/scoring.py` documents the mechanism precisely: a negative
#: enters the **same softmax** as an extra competitor, so *"a softmax denominator
#: is shared, and a negative that matches strongly suppresses the confidence of
#: every category, not only the one whose prompt set it belongs to."*
#:
#: A first pass here used three universal negatives — *"an ordinary intact road
#: surface"*, *"a clean and undamaged street"* — attached to all eight nodes.
#: Every street photograph matches those, so all eight contrast logits ran high,
#: every category's confidence collapsed toward 1/8, and the classifier abstained
#: on **everything** at 0.120 against a 0.150 floor. Which is the scoring layer
#: behaving exactly as written and the prompts being authored wrongly.
#:
#: So a negative here is a *confusable neighbour of its own category*: the thing
#: that looks like this defect and is not one. A shadow is not a pothole; a
#: closed manhole is not an open drain; a lamp that is simply off in daylight is
#: not a broken one.
_PROMPTS: Final[dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "roads.pothole": (
        (
            "a pothole in a road surface",
            "a deep hole in the asphalt of a street",
            "broken tarmac with loose stones around a cavity",
        ),
        (
            "a dark shadow lying across an intact road",
            "a metal manhole cover set flush into a road",
            "a tar patch repairing a road surface",
        ),
    ),
    "roads.footpath": (
        (
            "a broken and uneven footpath",
            "cracked paving slabs on a pavement",
            "a missing paving stone leaving a hole in a walkway",
        ),
        (
            "a level paved footpath in good repair",
            "a kerb ramp built into a pavement",
            "a painted line across a pavement",
        ),
    ),
    "water.leak": (
        (
            "water leaking from a burst pipe in a street",
            "a jet of water spraying from a broken water main",
            "water spreading across a road from a damaged pipe",
        ),
        (
            "a road wet from rainfall",
            "a street cleaning vehicle spraying water",
            "an intact water pipe with no leak",
        ),
    ),
    "water.drain": (
        (
            "an open drain with no cover",
            "a manhole with its cover removed",
            "sewage overflowing from a street drain",
        ),
        (
            "a closed manhole cover in a road",
            "a storm grating draining normally",
            "a drain being cleaned by workers",
        ),
    ),
    "light.out": (
        (
            "an unlit street lamp at night",
            "a dark street with a lamp that is not working",
            "a street light that fails to illuminate the road",
        ),
        (
            "a street lamp lit at night",
            "a street lamp switched off in daylight",
            "a dark street with no lamp post at all",
        ),
    ),
    "light.exposed": (
        (
            "exposed electrical wiring on a street pole",
            "an open electrical junction box with cables hanging out",
            "a live cable dangling above a footpath",
        ),
        (
            "a sealed electrical junction box on a pole",
            "telephone cables running neatly along a pole",
            "an electrician working on a closed panel",
        ),
    ),
    "waste.dumping": (
        (
            "rubbish dumped illegally beside a road",
            "a pile of construction debris left on a pavement",
            "household waste tipped onto open ground",
        ),
        (
            "a tidy street with no rubbish",
            "waste stacked at a designated collection point",
            "a rubbish truck collecting refuse",
        ),
    ),
    "waste.uncollected": (
        (
            "an overflowing rubbish bin",
            "refuse bags piled beside a full container",
            "a waste bin that has not been emptied",
        ),
        (
            "an empty rubbish bin with its lid closed",
            "a bin being emptied into a truck",
            "a clean waste container at a collection point",
        ),
    ),
}


def _prompt_sets() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for node_key, (positives, negatives) in _PROMPTS.items():
        for encoder in ("clip", "text"):
            specs.append(
                {
                    "node_key": node_key,
                    "locale": "en",
                    "encoder": encoder,
                    "prompts": list(positives),
                    "negative_prompts": list(negatives),
                    "prompt_set_version": PROMPT_VERSION,
                }
            )
    return specs


CALENDARS: Final[list[dict[str, Any]]] = [
    {
        "code": "municipal",
        "name": "Municipal office hours",
        "timezone": "Asia/Kolkata",
        "is_default": True,
        # ISO weekdays as strings: Monday is "1", Sunday is "7". Not day names
        # — `CalendarSpec` refuses those, and it is right to: a name is a
        # locale-dependent key on a tenant that may be administered in Marathi.
        "working_hours": {str(day): [{"start": "09:30", "end": "18:00"}] for day in range(1, 6)},
    },
    {
        "code": "emergency",
        "name": "Emergency response",
        "timezone": "Asia/Kolkata",
        "is_continuous": True,
    },
]


# ---------------------------------------------------------------------------
# The reports — a city with a week behind it
# ---------------------------------------------------------------------------
#
# **Every one goes through `POST /api/v1/complaints`.** Not a fixture loader and
# not an INSERT: the demo's complaints are recorded by the same handler a
# citizen's phone reaches, so they carry real hash chains, real projections, real
# outbox rows, and whatever the real pipeline decided about them. A demo whose
# data was written around the product is a demo that proves nothing about it —
# and this repository's whole proposition is that the record can be checked.
#
# The consequence is worth stating: **the seeder does not choose the outcomes.**
# It chooses what was reported and where. Whether a report classifies, clusters,
# abstains, or trips the abuse detector is the pipeline's answer, and the
# citizen surfaces render whatever that turns out to be. Some seeded reports
# park at `pending_classification`; that is §24.2's third outcome appearing in
# the demo because it appears in the product.

#: What a citizen typed, per category. Written the way people actually report —
#: a place, a problem, and often a reason it matters — rather than as prompt
#: bait. The text encoder scores these against the prompt sets above, so a
#: description that reads like a person wrote it is also the honest test of
#: whether the prompts work.
_DESCRIPTIONS: Final[dict[str, tuple[str, ...]]] = {
    "roads.pothole": (
        "Deep pothole right where the school bus stops. Two scooters went down last week.",
        "Big hole in the road surface, fills with water and you cannot see how deep it is.",
        "The tarmac has broken up across the whole lane. Autos swerve into oncoming traffic.",
        "Pothole outside the clinic gate. An ambulance has to slow to walking pace.",
    ),
    "roads.footpath": (
        "Paving slabs are cracked and lifting. My mother uses a stick and cannot walk here.",
        "A slab is missing from the footpath and there is a hole straight down.",
        "The whole pavement is uneven outside the bank. People walk in the road instead.",
    ),
    "water.leak": (
        "Water has been running from a burst pipe since Tuesday. Nobody has come.",
        "There is a leak spraying from the main and the road is flooded every morning.",
        "Pipeline leaking beside the junction. Clean water going straight into the drain.",
    ),
    "water.drain": (
        "The drain cover is gone. It is a straight drop and there is no barrier around it.",
        "Open drain outside the market, overflowing, and the smell is unbearable.",
        "Manhole has been open for three days. Someone has put a branch in it as a warning.",
    ),
    "light.out": (
        "The street light has not worked for a month. The whole lane is dark after seven.",
        "Two lamps out on this stretch. Women walking home from the station avoid it now.",
        "Street light is dead outside the temple. It was reported before and nothing happened.",
    ),
    "light.exposed": (
        "The junction box is hanging open with live wires inside, at child height.",
        "Cables are hanging loose from the pole and one of them is touching the wall.",
        "Exposed wiring on the pole next to the tea stall. It sparks when it rains.",
    ),
    "waste.dumping": (
        "Someone has tipped construction debris on the corner. It has been there a week.",
        "Rubbish dumped on the open plot again. Dogs pull it across the road every night.",
        "Household waste thrown beside the road, right next to where children play.",
    ),
    "waste.uncollected": (
        "Bin has not been emptied for five days and it is overflowing onto the footpath.",
        "Rubbish bags piled beside the container. The collection truck skipped this street.",
        "The bin is full and the lid will not close. Flies everywhere in this heat.",
    ),
}


#: How many reports describe the *same* incident, for the clusters that exist to
#: demonstrate §E17.2's payoff — *"You're the 4th person to report this."*
#:
#: That sentence is the citizen product's single best moment and it cannot be
#: staged: it is generated by the real dedup engine from real reports at real
#: coordinates. So the seeder plants a few genuine crowds — several people, from
#: several devices, reporting one pothole within a few metres of each other —
#: and lets the engine decide whether they are the same thing.
_CROWD_SIZES: Final[tuple[int, ...]] = (4, 3, 5, 2)

#: Metres of scatter within a crowd, converted to degrees at Pune's latitude.
#: Fifteen metres is several people standing around one hole, which is the case
#: dedup's Stage 1 `ST_DWithin` filter is tuned for.
_CROWD_SCATTER_DEG: Final = 15 / 111_000


def _check_no_overlap() -> None:
    """Two sibling wards must not both contain the same point.

    Asserted rather than eyeballed, because getting it wrong is invisible: the
    endpoint's tie-break would resolve the ambiguity deterministically and
    nobody would notice that a citizen in Shivajinagar was being told they were
    in Deccan Gymkhana half the time. A locality is exempt — it nests inside its
    ward on purpose, and that nesting is the thing worth demonstrating.
    """
    boxes = [(code, lat, lng, half) for code, _, _, _, lat, lng, kind, half in WARDS if kind == "ward"]
    for index, (code_a, lat_a, lng_a, half_a) in enumerate(boxes):
        for code_b, lat_b, lng_b, half_b in boxes[index + 1 :]:
            if abs(lat_a - lat_b) < half_a + half_b and abs(lng_a - lng_b) < half_a + half_b:
                raise SystemExit(
                    f"{FAIL} seeded wards {code_a} and {code_b} overlap. Shrink _HALF or move a "
                    f"centroid: two wards containing one point makes the Place card arbitrary."
                )


def _box(latitude: float, longitude: float, half: float) -> list[list[list[float]]]:
    """A closed ring around a centroid, in GeoJSON `[lng, lat]` order.

    The order catches people out — including, once, this function — because
    every other coordinate in this product is stated latitude-first, in the
    order a person says it. GeoJSON and PostGIS both take `x, y`, and `x` is
    longitude.
    """
    west, east = longitude - half, longitude + half
    south, north = latitude - half, latitude + half
    return [
        [
            [west, south],
            [east, south],
            [east, north],
            [west, north],
            [west, south],
        ]
    ]


def _zone_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {
            "code": "CITY",
            "name": "Pune",
            "kind": "city",
            "translations": {"mr": "पुणे"},
        }
    ]
    for code, name, marathi in ZONES:
        specs.append(
            {
                "code": code,
                "name": name,
                "kind": "zone",
                "parent_code": "CITY",
                "translations": {"mr": marathi},
            }
        )
    for code, name, marathi, parent, latitude, longitude, kind, half in WARDS:
        specs.append(
            {
                "code": code,
                "name": name,
                "kind": kind,
                "parent_code": parent,
                "translations": {"mr": marathi},
                # A MultiPolygon: a list of polygons, each a list of rings.
                "boundary": [_box(latitude, longitude, half)],
                "attributes": {
                    # Stated in the data, not only in this file's docstring. A
                    # reader querying the tenant should be able to learn that
                    # these are approximations without reading the seeder.
                    "boundary_source": "approximate-demo",
                    "boundary_note": (
                        "An axis-aligned box around the ward centroid, seeded for local "
                        "development. Not a survey boundary. Replace with the authority's "
                        "own shapefile before this tenant means anything."
                    ),
                    "centroid_lat": latitude,
                    "centroid_lng": longitude,
                },
            }
        )
    return specs


def _request(
    method: str, url: str, *, headers: dict[str, str] | None = None, body: Any = None
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
            return response.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, payload
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)


def _multipart(fields: dict[str, str], photo: bytes, filename: str) -> tuple[bytes, str]:
    """Build a multipart body by hand.

    No `requests`, no `httpx`: `scripts/` runs on a bare interpreter — that is
    what lets `nem doctor` and the gate scripts work on a laptop that has not
    installed the backend's dependency set — and the standard library has no
    multipart encoder. Forty lines here beats a dependency in every gate script.
    """
    boundary = f"----nemesis{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
            f"Content-Type: image/jpeg\r\n\r\n"
        ).encode()
    )
    parts.append(photo)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _submit(tenant_id: str, fields: dict[str, str], photo: bytes, filename: str) -> tuple[int, Any]:
    body, content_type = _multipart(fields, photo, filename)
    request = urllib.request.Request(f"{BASE}/api/v1/complaints", data=body, method="POST")
    request.add_header("Content-Type", content_type)
    request.add_header(TENANT_HEADER, tenant_id)
    # A key per report, so a re-run of this script is a *new* set of reports
    # rather than a replay of the old ones. Deliberate: the point of `--reports`
    # is to add a week of history, and an idempotent seeder would silently do
    # nothing the second time somebody asked for more.
    request.add_header("Idempotency-Key", str(uuid.uuid4()))
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read().decode("utf-8")
            return response.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, payload
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)


def _seed_reports(tenant_id: str, count: int, seed: int) -> int:
    """Submit `count` reports across the city, including a few real crowds.

    **Every report comes from a different device fingerprint.** §11.3's abuse
    detector counts submissions per device inside a window, and a seeder that
    used one identity would trip it on report four — flagging the whole demo city
    as coordinated abuse, which is the detector working and the seeder lying
    about who reported what. One citizen, one device, which is also what the data
    is supposed to represent.
    """
    from demo_imagery import RENDERERS, render

    rng = random.Random(seed)
    categories = [key for key in RENDERERS if key in _DESCRIPTIONS]
    wards = [ward for ward in WARDS if ward[6] == "ward"]

    plan: list[tuple[str, float, float]] = []

    # A few genuine crowds first — several people, one incident, a few metres
    # apart. This is what §E17.2's "you're the 4th person" is generated from.
    for size in _CROWD_SIZES:
        if len(plan) + size > count:
            break
        ward = rng.choice(wards)
        category = rng.choice(categories)
        lat = ward[4] + rng.uniform(-_HALF * 0.6, _HALF * 0.6)
        lng = ward[5] + rng.uniform(-_HALF * 0.6, _HALF * 0.6)
        for _ in range(size):
            plan.append(
                (
                    category,
                    lat + rng.uniform(-_CROWD_SCATTER_DEG, _CROWD_SCATTER_DEG),
                    lng + rng.uniform(-_CROWD_SCATTER_DEG, _CROWD_SCATTER_DEG),
                )
            )

    # The rest are scattered singles, one per ward in rotation so no ward is
    # empty on the public surface and none is implausibly busy.
    while len(plan) < count:
        ward = wards[len(plan) % len(wards)]
        plan.append(
            (
                rng.choice(categories),
                ward[4] + rng.uniform(-_HALF * 0.8, _HALF * 0.8),
                ward[5] + rng.uniform(-_HALF * 0.8, _HALF * 0.8),
            )
        )

    rng.shuffle(plan)

    accepted = 0
    refused: list[str] = []
    for index, (category, lat, lng) in enumerate(plan):
        descriptions = _DESCRIPTIONS[category]
        fields = {
            "latitude": f"{lat:.6f}",
            "longitude": f"{lng:.6f}",
            # One citizen, one device — see the docstring.
            "device_fingerprint": f"demo-{uuid.uuid4().hex[:16]}",
            "description_text": descriptions[index % len(descriptions)],
            "locale": "en",
        }
        status, body = _submit(
            tenant_id, fields, render(category, seed + index), f"report-{index}.jpg"
        )
        if status == 202:
            accepted += 1
            sys.stdout.write(f"\r     submitted {accepted}/{len(plan)}")
            sys.stdout.flush()
        else:
            refused.append(f"{category}: {status} {body}")

    sys.stdout.write("\r" + " " * 40 + "\r")

    if refused:
        sys.stderr.write(f"{FAIL} {len(refused)} report(s) were refused:\n")
        for failure in refused[:5]:
            sys.stderr.write(f"       {failure}\n")
        return 1

    sys.stdout.write(
        f"{OK} {accepted} reports submitted across {len(wards)} wards, "
        f"including {sum(_CROWD_SIZES[: len(_CROWD_SIZES)])} in {len(_CROWD_SIZES)} crowds.\n"
        f"     The pipeline is processing them now — classification, dedup and\n"
        f"     redaction run per report, so the city fills in over a minute or two.\n"
    )
    return 0


def _attach_prompt_sets(slug: str, admin: dict[str, str], supplied_id: str | None) -> int:
    """Apply the prompt sets to a tenant that already exists.

    One `PUT` per set, because that is the shape the control plane publishes —
    `PromptSetSpec` per request rather than a batch. Slower, and honest: a batch
    endpoint that does not exist is not something a seeding script should invent
    a client for.
    """
    tenant_id = _tenant_id(slug, supplied_id)
    if tenant_id is None:
        sys.stderr.write(
            f"{FAIL} '{slug}' already exists and its id cannot be resolved over HTTP.\n"
            f"       No control-plane endpoint maps a slug to an id, and the public\n"
            f"       index only answers for a tenant that opted in (ADR-0021).\n"
            f"       Pass it explicitly:\n"
            f"         docker compose exec postgres psql -U nemesis -d nemesis -t \\\n"
            f"           -c \"select id from tenants where slug = '{slug}'\"\n"
            f"         nem seed-demo --tenant-id <id>\n"
        )
        return 1

    headers = {**admin, TENANT_HEADER: tenant_id}
    failures: list[str] = []
    for spec in _prompt_sets():
        status, body = _request(
            "PUT", f"{CONTROL_PLANE}/taxonomy/prompt-sets", headers=headers, body=spec
        )
        if status not in (200, 201):
            failures.append(f"{spec['node_key']}/{spec['encoder']}: {status} {body}")

    if failures:
        sys.stderr.write(f"{FAIL} {len(failures)} prompt set(s) were refused:\n")
        for failure in failures[:5]:
            sys.stderr.write(f"       {failure}\n")
        return 1

    sys.stdout.write(
        f"{OK} '{slug}' already existed; {len(_prompt_sets())} prompt sets re-applied.\n"
        f"     tenant id: {tenant_id}\n"
    )
    return 0


def _tenant_id(slug: str, supplied: str | None = None) -> str | None:
    """A tenant's id, over HTTP — or supplied, when HTTP cannot answer.

    **There is no control-plane endpoint that resolves a slug to an id**, which
    is a real gap and is stated here rather than worked around with SQL: this
    script talks to the API and to nothing else, and that constraint is what
    makes it a demonstration of Phase 5 rather than a fixture loader wearing
    one's clothes.

    The public zone index *does* publish `tenant`, and it is tried first — but
    only for a tenant that has opted into the public API, which ADR-0021 makes
    a deliberate per-customer decision defaulting to off. A freshly provisioned
    demo tenant has not opted in and cannot, because `TenantSpec` has no field
    for it and no endpoint sets it. So the fallback is `--tenant-id`, and the
    message below says exactly where to find it.
    """
    if supplied is not None and supplied != "":
        return supplied

    status, body = _request("GET", f"{BASE}/api/v1/public/{slug}/zones")
    if status == 200 and isinstance(body, dict):
        tenant = body.get("tenant")
        if isinstance(tenant, str) and tenant:
            return tenant
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", default="pune-demo", help="tenant slug (default: pune-demo)")
    parser.add_argument("--name", default="Pune (demo)", help="tenant display name")
    parser.add_argument(
        "--reports",
        type=int,
        default=0,
        help=(
            "also submit this many citizen reports across the city, through the "
            "real submission endpoint. Adds to whatever is already there."
        ),
    )
    parser.add_argument(
        "--report-seed",
        type=int,
        default=7,
        help="seed for the report plan and its imagery, so a demo reproduces.",
    )
    parser.add_argument(
        "--tenant-id",
        default=None,
        help=(
            "the existing tenant's id, for a re-run against an already-provisioned "
            "slug. Only needed because no endpoint resolves a slug to an id."
        ),
    )
    args = parser.parse_args(argv)

    _check_no_overlap()

    token = os.environ.get("NEMESIS_CONTROL_PLANE_TOKEN", DEFAULT_TOKEN)
    admin = {TOKEN_HEADER: token}

    status, body = _request(
        "POST",
        f"{CONTROL_PLANE}/tenants",
        headers=admin,
        body={
            "tenant": {
                "slug": args.slug,
                "name": args.name,
                # Two scripts, because §E10.1 makes Devanagari a design partner
                # rather than a fallback — and a per-script type scale nobody
                # has rendered real Marathi through is a scale nobody has
                # actually looked at.
                "locales": ["en", "mr"],
                "primary_locale": "en",
                "timezone": "Asia/Kolkata",
            },
            "departments": DEPARTMENTS,
            "taxonomy": TAXONOMY,
            "calendars": CALENDARS,
            "zones": _zone_specs(),
            "prompt_sets": _prompt_sets(),
        },
    )

    if status == 409:
        # Already provisioned. **Not a no-op**: prompt sets are the one part of
        # this a running tenant is most likely to be missing — they were omitted
        # from the first version of this script, and a tenant with a taxonomy and
        # no prompts abstains on every classification — so they are re-applied
        # through the same endpoint the control plane exposes for it. Idempotent
        # by (node_key, locale, encoder), so a second run costs nothing.
        rc = _attach_prompt_sets(args.slug, admin, args.tenant_id)
        if rc != 0 or args.reports <= 0:
            return rc
        existing = _tenant_id(args.slug, args.tenant_id)
        if existing is None:
            return 1
        return _seed_reports(existing, args.reports, args.report_seed)

    if status != 201 or not isinstance(body, dict):
        sys.stderr.write(f"{FAIL} provisioning failed - status {status}: {body}\n")
        return 1

    tenant_id = body.get("tenant_id")
    sys.stdout.write(
        f"{OK} '{args.slug}' provisioned.\n"
        f"     tenant id: {tenant_id}\n"
        f"     {len(WARDS)} wards with approximate boundaries, "
        f"{len(TAXONOMY)} taxonomy nodes, locales en + mr.\n\n"
        f"     Point the frontend at it - frontend/.env.local:\n"
        f"       NEMESIS_TENANT_ID={tenant_id}\n"
    )

    if args.reports > 0 and isinstance(tenant_id, str):
        return _seed_reports(tenant_id, args.reports, args.report_seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
