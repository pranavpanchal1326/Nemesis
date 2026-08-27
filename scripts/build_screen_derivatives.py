"""Turn the captured PNGs into the two sets of images the docs actually use.

``frontend/scripts/capture-product-shots.ts`` photographs the running product at
1.5x-3x device scale, which is the right thing for a capture and the wrong thing
for a file anybody has to download: the landing frames come off the browser at
four megabytes each. Nothing downstream needs that.

Two derivatives, because the README and the deck want different crops of the
same frame:

``assets/screens/*.jpg``
    The whole frame, capped at 1600px wide. What the README embeds — a
    full-page shot scrolls in a browser, so its height is a feature there.

``assets/screens/deck/*.jpg``
    The same frame cropped to the top of the page for the wide ones, because a
    3000px-tall screenshot placed on a 16:9 slide is a legible strip of nothing.
    Phone frames are never cropped: their shape is the point.

Run after a capture:

    python scripts/build_screen_derivatives.py [--keep-png]
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SCREENS = ROOT / "assets" / "screens"
DECK = SCREENS / "deck"

#: What the README embeds. Wide enough for a two-up table, small enough to clone.
README_WIDTH = 1600
#: What the deck places. A 16:9 slide at 96dpi is 1280px; 1800 leaves headroom.
DECK_WIDTH = 1800
#: The tallest a deck image may be, as a multiple of its width — 16:10, which
#: is the shape of the image well every deck slide reserves. Past this a
#: slide shows a ribbon, so the frame is cropped to its top instead.
DECK_MAX_RATIO = 0.625

JPEG = {"quality": 88, "optimize": True, "progressive": True, "subsampling": "4:2:0"}


def _resize(image: Image.Image, width: int) -> Image.Image:
    if image.width <= width:
        return image
    height = round(image.height * width / image.width)
    return image.resize((width, height), Image.LANCZOS)


def _trim_bottom(image: Image.Image, floor_ratio: float) -> Image.Image:
    """Crop the dead ground off the bottom of a frame.

    A viewport screenshot of a short page — the Place card, the evidence trail —
    is mostly paper below the fold, and a slide that reserves half its image
    well for an empty background is a slide with a hole in it. Only the bottom
    is trimmed: the top of a page is its masthead, and the sides are its margins.
    Both are composition rather than waste.

    ``floor_ratio`` is the shortest the result may be, as a multiple of its
    width. A phone frame keeps a phone's proportions even when its screen is
    half empty — the Place card is two controls on a tall device, and trimming
    it to the ink turns a photograph of a phone into a cropped rectangle that
    could be anything.
    """
    ground = image.getpixel((image.width - 2, image.height - 2))
    if not isinstance(ground, tuple):
        return image

    pixels = image.load()
    if pixels is None:
        return image

    #: How far a pixel has to be from the corner colour to count as content.
    #: Above the paper stock's own printed grain, below any real ink.
    tolerance = 14
    step = max(1, image.width // 220)

    last = 0
    for y in range(image.height - 1, -1, -1):
        row_has_ink = False
        for x in range(0, image.width, step):
            pixel = pixels[x, y]
            if max(abs(pixel[i] - ground[i]) for i in range(3)) > tolerance:
                row_has_ink = True
                break
        if row_has_ink:
            last = y
            break

    if last == 0:
        return image

    floor = round(image.width * floor_ratio)
    height = min(image.height, max(last + round(image.width * 0.03), floor))
    return image if height >= image.height else image.crop((0, 0, image.width, height))


def _flatten(image: Image.Image) -> Image.Image:
    """JPEG has no alpha. Composite onto paper-50 rather than onto black."""
    if image.mode not in {"RGBA", "LA", "P"}:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    ground = Image.new("RGB", rgba.size, (244, 237, 225))
    ground.paste(rgba, mask=rgba.split()[3])
    return ground


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-png", action="store_true", help="leave the originals in place")
    options = parser.parse_args()

    manifest_path = SCREENS / "shots.json"
    if not manifest_path.exists():
        print(f"no manifest at {manifest_path} — run the capture first")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    DECK.mkdir(parents=True, exist_ok=True)

    written = 0
    for shot in manifest:
        source = SCREENS / shot["file"]
        if not source.exists():
            print(f"  missing  {shot['file']}")
            continue

        stem = source.stem
        with Image.open(source) as raw:
            phone = shot["surface"] == "phone"
            frame = _trim_bottom(_flatten(raw), 1.7 if phone else 0.42)

            _resize(frame, README_WIDTH).save(SCREENS / f"{stem}.jpg", "JPEG", **JPEG)

            deck = frame
            if not phone:
                limit = round(deck.width * DECK_MAX_RATIO)
                if deck.height > limit:
                    deck = deck.crop((0, 0, deck.width, limit))
            _resize(deck, DECK_WIDTH).save(DECK / f"{stem}.jpg", "JPEG", **JPEG)

        shot["image"] = f"{stem}.jpg"
        written += 1
        if not options.keep_png:
            source.unlink()

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", "utf-8")

    total = sum(p.stat().st_size for p in SCREENS.rglob("*.jpg"))
    print(f"wrote {written} frames in two sizes — {total / 1_048_576:.1f} MB total")
    if not options.keep_png:
        # A stray PNG here is a four-megabyte file nobody meant to commit.
        for orphan in SCREENS.glob("*.png"):
            print(f"  orphan PNG left in place: {orphan.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
