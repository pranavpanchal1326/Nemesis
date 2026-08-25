"""Fetch, subset and self-host the type stack — §E10, §E10.1, §6 Principle #6.

The blueprint is unambiguous: *"Six faces, six jobs, **all self-hosted** — no
CDN, satisfying §6 Principle #6 (zero-cost, self-hosted, offline-capable) at the
type layer."* A font fetched at page load from a third party breaks the
air-gapped bootstrap Phase 29 gates on, and it is the one dependency nobody
notices until the demo has no network.

So this script runs **once, at build time**, on a machine that has a network,
and writes real files into ``frontend/public/fonts/``. The shipped application
fetches nothing: ``scripts/check-guards.ts`` fails the build on a CDN URL in
``src/``, and a Playwright assertion fails it if a request for a font ever
leaves the origin.

**Why Python, in a TypeScript workspace.** §E10 asks for subsetting with
``fonttools``, which is a Python tool, and this repository already runs its
host-side checks on a bare interpreter. The alternative — a second subsetting
implementation in Node — would be a worse answer to the same question.

**Two sources, two treatments, for a stated reason.**

* **Fontshare (ITF)** ships one file per family covering the whole Latin
  repertoire. We subset it here with ``fonttools``.
* **Google Fonts** already serves *per-script* subsets, cut by the same
  pipeline that produced the font, with correct ``unicode-range`` blocks. Cutting
  those again by hand would be strictly worse. We take the files, and we take
  the ranges with them.

Usage::

    nem web-fonts              fetch, subset, write
    nem web-fonts --verify     assert every declared face is present on disk
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# The Windows console defaults to cp1252, and this script prints an arrow and a
# rupee sign. Reconfiguring is one line; discovering it as a traceback halfway
# through a 30 MB download is not.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "public" / "fonts"
CSS_OUT = ROOT / "src" / "design" / "generated" / "fonts.css"

# A browser user agent, because the Google Fonts CSS API serves woff2 only to
# clients it believes can read it. Stating this plainly rather than leaving a
# mysterious header for the next person to delete.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# Latin + Latin Extended, merged. The merge is deliberate: ₹ (U+20B9) lives in
# the extended range, and a civic budget screen in Maharashtra that cannot print
# a rupee sign is not a subset, it is a bug.
LATIN_RANGE = (
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,"
    "U+0100-02AF,U+0304,U+0308,U+0329,U+1E00-1E9F,U+1EF2-1EFF,"
    "U+2000-206F,U+2074,U+20A0-20C0,U+2113,U+2122,U+2190-2193,U+2212,U+2215,"
    "U+2713,U+25A0-25FF,U+FEFF,U+FFFD"
)

#: Which Google subsets we keep. Everything else is a script this product does
#: not typeset today, and shipping it would be dead weight on a 2G profile.
WANTED_GOOGLE_SUBSETS = {"latin", "latin-ext", "devanagari"}


@dataclass(frozen=True)
class Face:
    """One face, one job (§E10)."""

    #: The `--font-*` token this face fulfils, from src/design/tokens.json.
    role: str
    family: str
    why: str
    licence: str
    licence_url: str
    #: "fontshare" or "google"
    source: str
    #: Fontshare slug, or the Google Fonts family query.
    ref: str
    #: Fontshare: style names to take. Google: the css2 `wght` spec.
    styles: tuple[str, ...] = ()
    google_query: str = ""
    variable: tuple[int, int] | None = None
    weights: tuple[int, ...] = field(default_factory=lambda: (400,))


FACES: tuple[Face, ...] = (
    Face(
        role="narrative",
        family="Gambarino",
        why="Condensed Garalde serif, very fine serifs, teardrop terminals. Editorial gravity with real personality — landing prose, report covers.",
        licence="ITF Free Font Licence",
        licence_url="https://www.fontshare.com/licence",
        source="fontshare",
        ref="gambarino",
        styles=("Regular",),
    ),
    Face(
        role="institutional",
        family="Panchang",
        why="Wide, panoramic, industrial — the municipal signage voice. WARD 14, act titles.",
        licence="ITF Free Font Licence",
        licence_url="https://www.fontshare.com/licence",
        source="fontshare",
        ref="panchang",
        styles=("Variable",),
        variable=(200, 800),
    ),
    Face(
        role="interface",
        family="Switzer",
        why="Neutral grotesque, variable, strong tabular figures at 13–15 px. The console workhorse.",
        licence="ITF Free Font Licence",
        licence_url="https://www.fontshare.com/licence",
        source="fontshare",
        ref="switzer",
        styles=("Variable",),
        variable=(100, 900),
    ),
    Face(
        role="data",
        family="JetBrains Mono",
        why="Ids, chain hashes, coordinates, timestamps. Every number in this product is tabular.",
        licence="SIL Open Font Licence 1.1",
        licence_url="https://openfontlicense.org/",
        source="google",
        ref="JetBrains Mono",
        google_query="JetBrains+Mono:wght@400;500;700",
    ),
    Face(
        role="document",
        family="Courier Prime",
        why="Typewriter is the visual language of officialdom — stamps, receipts, RTI drafts, case files.",
        licence="SIL Open Font Licence 1.1",
        licence_url="https://openfontlicense.org/",
        source="google",
        ref="Courier Prime",
        google_query="Courier+Prime:ital,wght@0,400;0,700;1,400",
    ),
    Face(
        role="hand",
        family="Kalam",
        why="The human hand — paper flags on pins, margin notes, citizen annotations. One hand across Latin AND Devanagari, which is why it is this face and not two.",
        licence="SIL Open Font Licence 1.1",
        licence_url="https://openfontlicense.org/",
        source="google",
        ref="Kalam",
        google_query="Kalam:wght@300;400;700",
    ),
    Face(
        role="deva-display",
        family="Sarpanch",
        why="Devanagari display. Squared, straight-lined, wide — it pairs with Panchang's width, which is what §E10.1 asked of the face it named.",
        licence="SIL Open Font Licence 1.1",
        licence_url="https://openfontlicense.org/",
        source="google",
        ref="Sarpanch",
        google_query="Sarpanch:wght@400;600;700",
    ),
    Face(
        role="deva-narrative",
        family="Tiro Devanagari Marathi",
        why="Purpose-built for Marathi; the closest thing to a serious long-form default.",
        licence="SIL Open Font Licence 1.1",
        licence_url="https://openfontlicense.org/",
        source="google",
        ref="Tiro Devanagari Marathi",
        google_query="Tiro+Devanagari+Marathi:ital@0;1",
    ),
    Face(
        role="deva-interface",
        family="Noto Sans Devanagari",
        why="Matches Switzer's neutrality at small sizes.",
        licence="SIL Open Font Licence 1.1",
        licence_url="https://openfontlicense.org/",
        source="google",
        ref="Noto Sans Devanagari",
        google_query="Noto+Sans+Devanagari:wght@400;500;700",
    ),
    Face(
        role="deva-celebration",
        family="Modak",
        why="Inflated, joyful. Used exactly once: closure confirmed (§E17.5).",
        licence="SIL Open Font Licence 1.1",
        licence_url="https://openfontlicense.org/",
        source="google",
        ref="Modak",
        google_query="Modak",
    ),
)


# --------------------------------------------------------------------------
# The share card's faces — §E18, and why they are a second format
# --------------------------------------------------------------------------

#: Where the OG faces are written. Under ``public/`` because ``ImageResponse``
#: reads them off the filesystem at request time and ``public/`` is the one
#: directory guaranteed to be present in a deployment.
OG_DIR = OUT_DIR / "og"


@dataclass(frozen=True)
class OgFace:
    """One face the §E18 share card needs, in a format satori can read.

    **Why this duplication exists, stated rather than discovered later.**
    `satori` — which `next/og` bundles, and which §E18 names — parses TTF, OTF
    and WOFF. It does **not** parse WOFF2, and the 51 files this script ships
    are all WOFF2 because that is the right format for a browser. Decompressing
    at request time is not available either: WOFF2 is not plain brotli, it is a
    container with a transformed ``glyf`` table, and no pure-Node decompressor
    ships in this dependency set.

    So the card's faces are built once, offline, from the WOFF2 already on disk
    — no network, so a clean checkout can rebuild them — and committed. The cost
    is ~360 kB in the repository, which is stated here because a cost nobody
    wrote down is a cost the next person assumes was unavoidable.

    The set is **four faces, not ten**. A share card is a heading, four figures
    and one sentence, in two scripts. Every face beyond that is weight paid on
    every clone for a composition that does not use it.
    """

    #: The WOFF2 in ``public/fonts`` this is built from.
    source: str
    #: Output basename, without extension.
    name: str
    #: Pin a variable font to this weight. ``None`` keeps the file as it is.
    instance: int | None
    why: str


OG_FACES: tuple[OgFace, ...] = (
    OgFace(
        source="panchang-variable.woff2",
        name="panchang-600",
        instance=600,
        why="the municipal signage voice — the city and the place name (§E10)",
    ),
    OgFace(
        source="switzer-variable.woff2",
        name="switzer-400",
        instance=400,
        why="the sentence; §22.2's disclaimer is prose and reads as prose",
    ),
    OgFace(
        source="jetbrains-mono-500-latin.woff2",
        name="jetbrains-mono-500",
        instance=500,
        why="every figure on the card, tabular by construction (§E10.2)",
    ),
    OgFace(
        source="noto-sans-devanagari-400-devanagari.woff2",
        name="noto-sans-devanagari-400",
        # Pinned even though the source file is already the 400 cut: Google
        # serves it as a variable font with the axis intact, and instancing
        # drops `fvar`/`gvar` — 336 kB to 187 kB for glyphs the card renders
        # identically either way.
        instance=400,
        why=(
            "Devanagari, both roles. Sarpanch would be the display pair, and at "
            "228 kB for one weight on a card it is not worth the clone"
        ),
    ),
)


def build_og_fonts() -> int:
    """Decompress and pin the card's faces. Offline, from what is already here.

    Runs at the end of a fetch *and* on its own via ``--og``, because the input
    is ``public/fonts`` rather than the network: somebody changing the card's
    type should not need a working connection to rebuild it.
    """
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    OG_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for face in OG_FACES:
        source = OUT_DIR / face.source
        if not source.exists():
            print(f"fonts: {face.source} is not on disk — run `nem web-fonts`", file=sys.stderr)
            return 1

        font = TTFont(source)
        if face.instance is not None and "fvar" in font:
            # `updateFontNames=False`: these faces carry no STAT axis values, so
            # the name-table rewrite raises. The name is irrelevant here — the
            # card names the family itself when it registers the buffer.
            font = instancer.instantiateVariableFont(
                font, {"wght": face.instance}, inplace=False, updateFontNames=False
            )
        # Clearing the flavor is what turns a WOFF2 back into a bare TTF.
        font.flavor = None
        target = OG_DIR / f"{face.name}.ttf"
        font.save(target)
        total += target.stat().st_size
        print(f"  og/{target.name}  {target.stat().st_size // 1024} kB — {face.why}")

    print(f"  {len(OG_FACES)} share-card faces, {total // 1024} kB")
    return 0


def _get(url: str, *, ua: str | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": ua} if ua else {})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return response.read()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# --------------------------------------------------------------------------
# Fontshare — one file per style, subset here
# --------------------------------------------------------------------------


def fetch_fontshare(face: Face) -> list[dict[str, object]]:
    catalogue = json.loads(_get("https://api.fontshare.com/v2/fonts?limit=100").decode())
    entry = next((f for f in catalogue["fonts"] if f["slug"] == face.ref), None)
    if entry is None:
        raise SystemExit(f"fonts: {face.family} is not in the Fontshare catalogue")

    blocks: list[dict[str, object]] = []
    for style in entry["styles"]:
        if style["weight"]["name"] not in face.styles:
            continue
        # The catalogue's `file` field is extension-less; the CDN keys the
        # format off the suffix. Discovered by probing, and recorded here so
        # the next person does not repeat the 404.
        url = style["file"]
        if url.startswith("//"):
            url = "https:" + url
        url += ".woff2"

        raw = _get(url)
        name = f"{slug(face.family)}-{slug(style['weight']['name'])}.woff2"
        target = OUT_DIR / name

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.woff2"
            source.write_bytes(raw)
            before = source.stat().st_size
            subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    "-m",
                    "fontTools.subset",
                    str(source),
                    f"--unicodes={LATIN_RANGE}",
                    "--layout-features=*",
                    "--flavor=woff2",
                    f"--output-file={target}",
                ],
                check=True,
                capture_output=True,
            )
        after = target.stat().st_size
        print(f"  {name}  {before // 1024} kB → {after // 1024} kB")

        weight = (
            f"{face.variable[0]} {face.variable[1]}"
            if face.variable
            else str(style["weight"]["weight"])
        )
        blocks.append(
            {
                "family": face.family,
                "weight": weight,
                "style": "italic" if style["is_italic"] else "normal",
                "file": name,
                "range": LATIN_RANGE,
            }
        )
    return blocks


# --------------------------------------------------------------------------
# Google Fonts — already cut per script; take the files and the ranges
# --------------------------------------------------------------------------

_BLOCK = re.compile(
    r"/\*\s*(?P<label>[a-z0-9-]+)\s*\*/\s*@font-face\s*\{(?P<body>[^}]*)\}",
    re.IGNORECASE,
)


def _field(body: str, name: str) -> str:
    match = re.search(rf"{name}:\s*([^;]+);", body)
    return match.group(1).strip().strip("'\"") if match else ""


def fetch_google(face: Face) -> list[dict[str, object]]:
    css = _get(
        f"https://fonts.googleapis.com/css2?family={face.google_query}&display=swap",
        ua=BROWSER_UA,
    ).decode()

    blocks: list[dict[str, object]] = []
    for match in _BLOCK.finditer(css):
        label = match.group("label").lower()
        if label not in WANTED_GOOGLE_SUBSETS:
            continue
        body = match.group("body")
        url_match = re.search(r"url\((https://[^)]+)\)", body)
        if url_match is None:
            continue

        weight = _field(body, "font-weight") or "400"
        style = _field(body, "font-style") or "normal"
        name = (
            f"{slug(face.family)}-{slug(weight)}"
            f"{'-italic' if style == 'italic' else ''}-{label}.woff2"
        )
        target = OUT_DIR / name
        target.write_bytes(_get(url_match.group(1), ua=BROWSER_UA))
        print(f"  {name}  {target.stat().st_size // 1024} kB")

        blocks.append(
            {
                "family": face.family,
                "weight": weight,
                "style": style,
                "file": name,
                "range": _field(body, "unicode-range"),
            }
        )
    return blocks


# --------------------------------------------------------------------------


def write_css(blocks: list[dict[str, object]]) -> None:
    lines = [
        "/* GENERATED by scripts/fetch_fonts.py. Do not edit.",
        "   §E10, §6 Principle #6 — every face is self-hosted; the running",
        "   application fetches nothing from a third party. */",
        "",
    ]
    for block in blocks:
        lines += [
            "@font-face {",
            f'  font-family: "{block["family"]}";',
            f"  font-style: {block['style']};",
            f"  font-weight: {block['weight']};",
            # `swap` and not `optional`: a government form that renders in a
            # fallback face is usable; a government form that renders in no face
            # is not. §E13's ladder says the same thing about every other layer.
            "  font-display: swap;",
            f'  src: url("/fonts/{block["file"]}") format("woff2");',
        ]
        if block["range"]:
            lines.append(f"  unicode-range: {block['range']};")
        lines += ["}", ""]

    CSS_OUT.write_text("\n".join(lines), encoding="utf-8", newline="\n")

    # Formatted by the same tool that checks it. Reimplementing Prettier's
    # line-wrapping in Python so `format:check` stays green would be a second
    # formatter to keep in step — the exact shape of drift this repository
    # refuses everywhere else.
    npx = shutil.which("npx")
    if npx is not None:
        subprocess.run(  # noqa: S603
            [npx, "prettier", "--write", str(CSS_OUT)],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
    print(f"  wrote {CSS_OUT.relative_to(ROOT)}")


def write_licences() -> None:
    lines = [
        "# Type — faces, jobs, and licences",
        "",
        "Generated by `scripts/fetch_fonts.py`. Every face here is self-hosted",
        "(§E10, §6 Principle #6) and free for commercial use. Sourced",
        "predominantly from the **Indian Type Foundry** — an Indian foundry for",
        "an Indian civic product, which is an argument §E10 makes explicitly and",
        "not a coincidence.",
        "",
        "| Role | Face | Why | Licence |",
        "|---|---|---|---|",
    ]
    for face in FACES:
        lines.append(
            f"| `--font-{face.role}` | **{face.family}** | {face.why} "
            f"| [{face.licence}]({face.licence_url}) |"
        )
    lines += [
        "",
        "## On Kaana",
        "",
        "§E10.1 names **Kaana** as the Devanagari display face. It is not in",
        "Fontshare's catalogue and is not obtainable under a free commercial",
        "licence, so it cannot ship. **Sarpanch** takes the role: Indian Type",
        "Foundry, OFL, Devanagari and Latin, squared and straight-lined and wide",
        "— which is what §E10.1 actually asked for when it said the face should",
        "be *\"built from straight lines and triangular geometries\"* and should",
        "*\"pair with Panchang's width\"*. The requirement is met; the name in the",
        "blueprint was not available. Recorded in §E2's own-errors table rather",
        "than swapped quietly.",
        "",
    ]
    (OUT_DIR / "LICENSES.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"  wrote public/fonts/LICENSES.md")


def verify() -> int:
    if not CSS_OUT.exists():
        print("fonts: not fetched — run `nem web-fonts`", file=sys.stderr)
        return 1
    css = CSS_OUT.read_text(encoding="utf-8")
    missing = [
        name for name in re.findall(r'url\("/fonts/([^"]+)"\)', css) if not (OUT_DIR / name).exists()
    ]
    families = {face.family for face in FACES}
    declared = set(re.findall(r'font-family: "([^"]+)"', css))
    absent = families - declared

    if missing or absent:
        for name in missing:
            print(f"fonts: declared but not on disk: {name}", file=sys.stderr)
        for family in sorted(absent):
            print(f"fonts: face never fetched: {family}", file=sys.stderr)
        return 1
    files = set(re.findall(r'url\("/fonts/([^"]+)"\)', css))

    # The share card's faces are not in the CSS — nothing loads them in a
    # browser — so they need their own assertion, or the one surface that reads
    # them fails at request time with an ENOENT nobody saw coming (§E18).
    absent_og = [face.name for face in OG_FACES if not (OG_DIR / f"{face.name}.ttf").exists()]
    if absent_og:
        for name in absent_og:
            print(f"fonts: share-card face missing: og/{name}.ttf — run `nem web-fonts --og`", file=sys.stderr)
        return 1

    print(
        f"fonts: {len(declared)} families, {len(files)} files, "
        f"{len(OG_FACES)} share-card faces, all present"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="assert the fetch already happened")
    parser.add_argument(
        "--og",
        action="store_true",
        help="rebuild only the §E18 share-card faces, from the WOFF2 already on disk. No network.",
    )
    args = parser.parse_args()

    if args.verify:
        return verify()
    if args.og:
        return build_og_fonts()

    if shutil.which(sys.executable) is None:  # pragma: no cover
        return 1
    try:
        import fontTools  # noqa: F401
    except ModuleNotFoundError:
        print(
            "fonts: fonttools is required for subsetting — `pip install 'fonttools[woff]'`",
            file=sys.stderr,
        )
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    blocks: list[dict[str, object]] = []
    for face in FACES:
        print(f"{face.family} ({face.role})")
        blocks += fetch_fontshare(face) if face.source == "fontshare" else fetch_google(face)

    write_css(blocks)
    write_licences()
    return build_og_fonts()


if __name__ == "__main__":
    raise SystemExit(main())
