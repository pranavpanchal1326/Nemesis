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




# --------------------------------------------------------------------------
# Metric-matched fallbacks — A1, §E10, §E15
# --------------------------------------------------------------------------

#: Marks the generated fallback section of ``fonts.css``. Everything between
#: these two lines is rewritten by :func:`build_fallback_faces`; everything
#: outside them is left alone, which is what lets ``--fallbacks`` run on its
#: own, offline, without re-fetching 51 files.
FALLBACK_BEGIN = "/* --- metric-matched fallbacks (A1) - generated; do not edit --- */"
FALLBACK_END = "/* --- end metric-matched fallbacks --- */"

#: Suffix appended to a real family to name its adjusted fallback. The same
#: string is asserted against ``src/design/tokens.json`` by :func:`verify`, so
#: the stack and the generated face cannot drift apart.
FALLBACK_SUFFIX = " Fallback"


@dataclass(frozen=True)
class FallbackFace:
    """One adjusted ``@font-face`` standing in for one real face (§E10).

    **What is overridden, and what deliberately is not.** This face declares
    ``ascent-override``, ``descent-override`` and ``line-gap-override``, and it
    does **not** declare ``size-adjust``. That is a decision with an argument,
    recorded in ADR-0053 and repeated here because the omission is exactly the
    kind of thing a later reader will "fix".

    The three that are declared are computed **entirely from the real face**
    and describe the line box in em units, so they hold whichever local face
    the browser actually resolves - Arial or Liberation Sans, Georgia or
    Gelasio, Nirmala UI or Lohit Devanagari. ``size-adjust`` is a *ratio*
    between two faces, so declaring it needs the metrics of the face on the
    reader's machine, which this script cannot measure and which differs
    between a Windows host and CI's Linux container. A ratio computed against a
    face that did not resolve is worse than no ratio: it scales text away from
    the real face's width rather than towards it.

    Line-box height is also the term that actually moves a paragraph. A width
    mismatch re-wraps a line; an ascent mismatch moves every line below it.
    """

    #: The ``--font-*`` role this stands in for.
    role: str
    #: The real family whose metrics are copied.
    family: str
    #: The woff2 in ``public/fonts`` the metrics are read from.
    source: str
    #: Installed faces to stand in, in order. Any of them is metrically
    #: normalised by the overrides, so this list is about *availability*
    #: (Windows, macOS, the Playwright container) and not about metrics.
    local: tuple[str, ...]


FALLBACK_FACES: tuple[FallbackFace, ...] = (
    FallbackFace(
        role="narrative",
        family="Gambarino",
        source="gambarino-regular.woff2",
        local=("Georgia", "Gelasio", "Liberation Serif", "Times New Roman", "DejaVu Serif"),
    ),
    FallbackFace(
        role="institutional",
        family="Panchang",
        source="panchang-variable.woff2",
        local=("Haettenschweiler", "Impact", "Liberation Sans Narrow", "DejaVu Sans Condensed"),
    ),
    FallbackFace(
        role="interface",
        family="Switzer",
        source="switzer-variable.woff2",
        local=("Segoe UI", "Helvetica Neue", "Arial", "Liberation Sans", "DejaVu Sans"),
    ),
    FallbackFace(
        role="data",
        family="JetBrains Mono",
        source="jetbrains-mono-400-latin.woff2",
        local=("Consolas", "SF Mono", "Liberation Mono", "DejaVu Sans Mono", "Courier New"),
    ),
    FallbackFace(
        role="document",
        family="Courier Prime",
        source="courier-prime-400-latin.woff2",
        local=("Courier New", "Liberation Mono", "Nimbus Mono PS", "DejaVu Sans Mono"),
    ),
    FallbackFace(
        role="hand",
        family="Kalam",
        source="kalam-400-latin.woff2",
        local=("Segoe Script", "Bradley Hand", "Comic Sans MS", "DejaVu Sans"),
    ),
    FallbackFace(
        role="deva-display",
        family="Sarpanch",
        source="sarpanch-400-devanagari.woff2",
        local=("Nirmala UI", "Kohinoor Devanagari", "Noto Sans Devanagari", "Lohit Devanagari"),
    ),
    FallbackFace(
        role="deva-narrative",
        family="Tiro Devanagari Marathi",
        source="tiro-devanagari-marathi-400-devanagari.woff2",
        local=("Noto Serif Devanagari", "Nirmala UI", "Kohinoor Devanagari", "Lohit Devanagari"),
    ),
    FallbackFace(
        role="deva-interface",
        family="Noto Sans Devanagari",
        source="noto-sans-devanagari-400-devanagari.woff2",
        local=("Nirmala UI", "Kohinoor Devanagari", "Lohit Devanagari"),
    ),
    FallbackFace(
        role="deva-celebration",
        family="Modak",
        source="modak-400-devanagari.woff2",
        local=("Nirmala UI", "Kohinoor Devanagari", "Lohit Devanagari"),
    ),
)

#: OS/2 fsSelection bit 7, USE_TYPO_METRICS. When it is set, the typographic
#: metrics are the ones the renderer is being told to use; when it is clear,
#: the near-universal behaviour is hhea. Both branches are implemented rather
#: than assuming the flag, because these ten faces do not agree about it and a
#: face whose hhea and typo metrics differ would otherwise be adjusted to a
#: line box nothing renders.
USE_TYPO_METRICS = 1 << 7


def read_metrics(path: Path) -> tuple[float, float, float]:
    """Ascent, descent and line gap of a real face, as fractions of the em."""
    from fontTools.ttLib import TTFont

    font = TTFont(path, lazy=True)
    upm = font["head"].unitsPerEm
    os2 = font["OS/2"]
    if os2.fsSelection & USE_TYPO_METRICS:
        ascent, descent, gap = os2.sTypoAscender, os2.sTypoDescender, os2.sTypoLineGap
    else:
        hhea = font["hhea"]
        ascent, descent, gap = hhea.ascent, hhea.descent, hhea.lineGap
    font.close()
    # CSS states all three as positive percentages of the em; `descent` is
    # negative in every font table that carries it.
    return ascent / upm, abs(descent) / upm, gap / upm


def _percent(value: float) -> str:
    return f"{round(value * 100, 2):g}%"


def _fallback_block(face: "FallbackFace", metrics: tuple[float, float, float]) -> list[str]:
    """The CSS for one adjusted face, as lines.

    One function, two callers: the generator writes it and the verifier
    recomputes it and asserts the file still says the same thing. A generated
    artefact checked only for *presence* drifts the first time a face is
    re-subset, which is precisely the hand-tuned override A1 refuses.
    """
    ascent, descent, gap = metrics
    local = ", ".join(f'local("{name}")' for name in face.local)
    return [
        "@font-face {",
        f'  font-family: "{face.family}{FALLBACK_SUFFIX}";',
        f"  src: {local};",
        f"  ascent-override: {_percent(ascent)};",
        f"  descent-override: {_percent(descent)};",
        f"  line-gap-override: {_percent(gap)};",
        "}",
    ]


def build_fallback_faces() -> int:
    """Rewrite the fallback section of ``fonts.css`` from the woff2 on disk.

    No network: the real faces are committed (see the ``web-fonts`` docstring),
    so these numbers are derived from the same bytes the browser will load. Run
    on its own with ``--fallbacks``, and at the end of every fetch.
    """
    if not CSS_OUT.exists():
        print("fonts: not fetched - run 'nem web-fonts'", file=sys.stderr)
        return 1

    lines = [
        FALLBACK_BEGIN,
        "/* A1 - §E10 metric-matched fallbacks. Ascent, descent and line gap are",
        "   copied from the real face, so a fallback occupies the same line box and",
        "   the swap does not move the paragraph below it. size-adjust is",
        "   deliberately absent - ADR-0053 states why. */",
        "",
    ]
    for face in FALLBACK_FACES:
        source = OUT_DIR / face.source
        if not source.exists():
            print(f"fonts: {face.source} is not on disk - run 'nem web-fonts'", file=sys.stderr)
            return 1
        ascent, descent, gap = read_metrics(source)
        lines += [*_fallback_block(face, (ascent, descent, gap)), ""]
        print(
            f"  {face.family}{FALLBACK_SUFFIX}  "
            f"asc {_percent(ascent)} / desc {_percent(descent)} / gap {_percent(gap)}"
        )
    lines.append(FALLBACK_END)

    css = CSS_OUT.read_text(encoding="utf-8")
    generated = "\n".join(lines)
    if FALLBACK_BEGIN in css:
        start = css.index(FALLBACK_BEGIN)
        end = css.index(FALLBACK_END) + len(FALLBACK_END)
        css = css[:start] + generated + css[end:]
    else:
        css = css.rstrip("\n") + "\n\n" + generated + "\n"
    CSS_OUT.write_text(css, encoding="utf-8", newline="\n")

    _prettier(CSS_OUT)
    print(f"  {len(FALLBACK_FACES)} metric-matched fallback faces")
    return 0


def stacks() -> dict[str, list[str]]:
    """The ``--font-*`` stacks, read from the token source rather than restated."""
    tokens = json.loads((ROOT / "src" / "design" / "tokens.json").read_text(encoding="utf-8"))
    return {
        role: list(definition["stack"])
        for role, definition in tokens["type"]["family"].items()
        if isinstance(definition, dict)
    }


def verify_fallbacks(css: str) -> list[str]:
    """Every adjusted face is declared, and every stack names it in the right place.

    The second half is the one that matters. A generated ``@font-face`` that
    nothing references is a face the browser never uses, and it would fail
    silently: the page would render in the unadjusted system face exactly as it
    does today, and the CSS would look like the work had been done.
    """
    problems: list[str] = []
    family_stacks = stacks()
    # Prettier reflows a long `src:` list across several lines, so the file is
    # compared with whitespace collapsed rather than line by line. The claim
    # being checked is the declaration, not its wrapping.
    flat = re.sub(r"\s+", " ", css)
    for face in FALLBACK_FACES:
        name = f"{face.family}{FALLBACK_SUFFIX}"
        source = OUT_DIR / face.source
        if not source.exists():
            problems.append(f"fonts: {face.source} is not on disk")
        else:
            expected = re.sub(r"\s+", " ", " ".join(_fallback_block(face, read_metrics(source))))
            if expected not in flat:
                problems.append(
                    f"fonts: the adjusted face for {face.family} does not match its metrics "
                    f"- run 'nem web-fonts --fallbacks'"
                )
        stack = family_stacks.get(face.role, [])
        if name not in stack:
            problems.append(f"fonts: --font-{face.role} does not list {name!r}")
        elif stack.index(name) != stack.index(face.family) + 1:
            problems.append(
                f"fonts: {name!r} must sit directly after {face.family!r} in --font-{face.role}"
            )
    return problems


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

    _prettier(CSS_OUT)
    print(f"  wrote {CSS_OUT.relative_to(ROOT)}")


def _prettier(path: Path) -> None:
    """Format generated CSS with the same tool that checks it.

    Reimplementing Prettier's line-wrapping in Python so `format:check` stays
    green would be a second formatter to keep in step — the exact shape of
    drift this repository refuses everywhere else.
    """
    npx = shutil.which("npx")
    if npx is not None:
        subprocess.run(  # noqa: S603
            [npx, "prettier", "--write", str(path)],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )


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

    fallback_problems = verify_fallbacks(css)

    if missing or absent or fallback_problems:
        for name in missing:
            print(f"fonts: declared but not on disk: {name}", file=sys.stderr)
        for family in sorted(absent):
            print(f"fonts: face never fetched: {family}", file=sys.stderr)
        for problem in fallback_problems:
            print(problem, file=sys.stderr)
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
        f"{len(OG_FACES)} share-card faces, "
        f"{len(FALLBACK_FACES)} metric-matched fallbacks, all present"
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
    parser.add_argument(
        "--fallbacks",
        action="store_true",
        help="recompute only the A1 metric-matched fallback faces, from the WOFF2 on disk. No network.",
    )
    args = parser.parse_args()

    if args.verify:
        return verify()
    if args.og:
        return build_og_fonts()
    if args.fallbacks:
        return build_fallback_faces()

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
    # Order matters: the fallback pass edits the file write_css just wrote, and
    # reads metrics from the woff2 this run has only just put on disk.
    if build_fallback_faces() != 0:
        return 1
    return build_og_fonts()


if __name__ == "__main__":
    raise SystemExit(main())
