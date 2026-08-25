"""Procedural photographs for the demo dataset — and an honest account of them.

**These are not photographs and the seeder says so.** They are procedurally
drawn approximations: a grey road surface with a dark irregular hole, a wet
sheen spreading from a pipe, an unlit lamp against a dusk sky. Every one is
generated from a seed, so a demo re-seeded next week produces the same city.

**Why generate at all rather than ship a folder of stock images.** Two reasons
and the second is the one that decides it. This repository does not ship
third-party media it has not licensed (§6 Principle #6's sibling argument), and
a demo whose evidence is somebody else's copyrighted street photography is a
demo that cannot be published. And §22.1's face-blur stage is a *real* pipeline
stage: seeding it with images of real people, to be redacted, would mean putting
photographs of identifiable people into a demo database in order to demonstrate
removing them.

**What this costs, stated.** A generated image is not a street, so the CLIP
tower scores it near chance. The classifier's answer for a seeded report is
therefore driven mostly by the citizen's *text*, which is the honest outcome for
synthetic evidence — and `image_weight = 0.45` in the calibration document is
exactly the knob that makes text the majority of the signal. Where a seeded
report abstains, that is the demo showing §24.2's third outcome rather than the
demo being broken, and the citizen surface has a designed rendering for it.
"""

from __future__ import annotations

import io
import math
import random
from typing import Final

#: 640×480. Big enough that the encoders see structure rather than a swatch,
#: small enough that seeding forty reports does not push forty megabytes through
#: the upload path.
SIZE: Final = (640, 480)


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


def _noise(image: object, draw: object, rng: random.Random, amount: int) -> None:
    """Scatter faint speckle, so nothing reads as a flat vector fill.

    A perfectly flat region is the single strongest signal that an image was not
    photographed, and it is also what makes an encoder's answer collapse toward
    the prior. This does not make the image real; it stops it being obviously
    synthetic in the one way that costs the most.
    """
    from PIL import ImageDraw  # noqa: F401  (typing only; draw is already one)

    width, height = SIZE
    for _ in range(amount):
        x, y = rng.randrange(width), rng.randrange(height)
        shade = rng.randint(-18, 18)
        draw.point((x, y), fill=(shade % 256, shade % 256, shade % 256))  # type: ignore[attr-defined]


def _road(draw: object, rng: random.Random) -> None:
    width, height = SIZE
    base = rng.randint(96, 118)
    for y in range(height):
        # A gentle gradient: tarmac lit from above, darker toward the viewer.
        shade = base + int(18 * (y / height))
        draw.line([(0, y), (width, y)], fill=(shade, shade, shade + 2))  # type: ignore[attr-defined]
    for _ in range(240):
        x, y = rng.randrange(width), rng.randrange(height)
        grit = rng.randint(70, 150)
        draw.ellipse(  # type: ignore[attr-defined]
            [x, y, x + rng.randint(1, 3), y + rng.randint(1, 3)], fill=(grit, grit, grit)
        )


def _blob(draw: object, rng: random.Random, cx: int, cy: int, radius: int, fill: tuple[int, int, int]) -> None:
    """An irregular closed shape. A circle reads as a drawing; this reads as damage."""
    points: list[tuple[float, float]] = []
    for step in range(18):
        angle = (step / 18) * math.tau
        wobble = radius * rng.uniform(0.62, 1.32)
        points.append((cx + math.cos(angle) * wobble, cy + math.sin(angle) * wobble * 0.72))
    draw.polygon(points, fill=fill)  # type: ignore[attr-defined]


def pothole(seed: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    rng = _rng(seed)
    image = Image.new("RGB", SIZE, (110, 110, 112))
    draw = ImageDraw.Draw(image)
    _road(draw, rng)
    cx, cy = rng.randint(220, 420), rng.randint(200, 320)
    radius = rng.randint(70, 120)
    _blob(draw, rng, cx, cy, radius + 10, (78, 74, 70))          # broken rim
    _blob(draw, rng, cx, cy, radius, (38, 34, 32))               # the hole
    _blob(draw, rng, cx + 8, cy + 6, radius // 2, (22, 20, 20))  # depth
    for _ in range(30):  # loose stones around the edge
        x = cx + rng.randint(-radius - 40, radius + 40)
        y = cy + rng.randint(-radius - 20, radius + 30)
        grit = rng.randint(120, 170)
        draw.ellipse([x, y, x + rng.randint(3, 8), y + rng.randint(3, 7)], fill=(grit, grit - 6, grit - 12))
    _noise(image, draw, rng, 5000)
    return _encode(image.filter(ImageFilter.GaussianBlur(0.6)))


def footpath(seed: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    rng = _rng(seed)
    image = Image.new("RGB", SIZE, (168, 162, 150))
    draw = ImageDraw.Draw(image)
    width, height = SIZE
    # Paving slabs, then one missing and two cracked.
    for row in range(6):
        for col in range(8):
            x, y = col * 84 - 20, row * 86 - 10
            shade = rng.randint(150, 186)
            draw.rectangle([x, y, x + 78, y + 80], fill=(shade, shade - 5, shade - 16), outline=(120, 116, 106))
    gap_x, gap_y = rng.randint(1, 6) * 84 - 20, rng.randint(1, 4) * 86 - 10
    draw.rectangle([gap_x, gap_y, gap_x + 78, gap_y + 80], fill=(74, 68, 58))
    _blob(draw, rng, gap_x + 40, gap_y + 40, 30, (52, 46, 40))
    for _ in range(6):  # cracks
        x, y = rng.randrange(width), rng.randrange(height)
        draw.line([(x, y), (x + rng.randint(-70, 70), y + rng.randint(-50, 50))], fill=(96, 90, 82), width=3)
    _noise(image, draw, rng, 4000)
    return _encode(image.filter(ImageFilter.GaussianBlur(0.5)))


def water_leak(seed: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    rng = _rng(seed)
    image = Image.new("RGB", SIZE, (118, 116, 112))
    draw = ImageDraw.Draw(image)
    _road(draw, rng)
    cx, cy = rng.randint(240, 400), rng.randint(230, 320)
    _blob(draw, rng, cx, cy, 150, (96, 116, 130))   # wet sheen
    _blob(draw, rng, cx, cy, 96, (110, 138, 156))
    _blob(draw, rng, cx, cy, 44, (150, 178, 196))   # standing water
    # The pipe.
    draw.rectangle([cx - 20, cy - 130, cx + 18, cy - 20], fill=(88, 84, 80), outline=(60, 58, 56))
    for _ in range(60):  # spray
        x = cx + rng.randint(-90, 90)
        y = cy - rng.randint(0, 120)
        draw.ellipse([x, y, x + 3, y + 3], fill=(196, 214, 226))
    _noise(image, draw, rng, 4500)
    return _encode(image.filter(ImageFilter.GaussianBlur(0.7)))


def open_drain(seed: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    rng = _rng(seed)
    image = Image.new("RGB", SIZE, (124, 120, 114))
    draw = ImageDraw.Draw(image)
    _road(draw, rng)
    cx, cy = rng.randint(250, 400), rng.randint(220, 310)
    draw.ellipse([cx - 96, cy - 62, cx + 96, cy + 62], fill=(64, 60, 54), outline=(92, 88, 80), width=6)
    draw.ellipse([cx - 78, cy - 48, cx + 78, cy + 48], fill=(24, 22, 20))
    # The displaced cover, leaning.
    draw.ellipse([cx + 70, cy + 10, cx + 190, cy + 78], fill=(88, 86, 84), outline=(58, 56, 54), width=4)
    _noise(image, draw, rng, 4200)
    return _encode(image.filter(ImageFilter.GaussianBlur(0.6)))


def street_light(seed: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    rng = _rng(seed)
    image = Image.new("RGB", SIZE, (26, 30, 46))
    draw = ImageDraw.Draw(image)
    width, height = SIZE
    for y in range(height):  # dusk gradient
        shade = 22 + int(38 * (1 - y / height))
        draw.line([(0, y), (width, y)], fill=(shade, shade + 4, shade + 20))
    draw.rectangle([0, height - 120, width, height], fill=(34, 34, 36))  # the road
    px = rng.randint(180, 440)
    draw.rectangle([px - 7, 60, px + 7, height - 110], fill=(46, 46, 50))  # the pole
    draw.line([(px, 62), (px + 74, 44)], fill=(46, 46, 50), width=12)      # the arm
    draw.ellipse([px + 54, 30, px + 108, 60], fill=(40, 40, 44), outline=(58, 58, 62))  # dark lamp
    _noise(image, draw, rng, 3000)
    return _encode(image.filter(ImageFilter.GaussianBlur(0.5)))


def exposed_wiring(seed: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    rng = _rng(seed)
    image = Image.new("RGB", SIZE, (146, 148, 152))
    draw = ImageDraw.Draw(image)
    width, height = SIZE
    for y in range(height):
        shade = 130 + int(46 * (y / height))
        draw.line([(0, y), (width, y)], fill=(shade, shade, shade - 4))
    px = rng.randint(230, 400)
    draw.rectangle([px - 22, 0, px + 22, height], fill=(96, 92, 86))  # the pole
    draw.rectangle([px - 46, 150, px + 46, 260], fill=(56, 56, 58), outline=(34, 34, 36), width=3)  # box
    draw.rectangle([px - 40, 156, px + 40, 210], fill=(28, 28, 30))  # the open face
    for _ in range(9):  # loose cables
        y0 = rng.randint(160, 250)
        colour = rng.choice([(180, 40, 36), (28, 28, 30), (196, 176, 60)])
        draw.line(
            [(px, y0), (px + rng.randint(60, 180), y0 + rng.randint(40, 170))],
            fill=colour,
            width=rng.randint(3, 6),
        )
    _noise(image, draw, rng, 3800)
    return _encode(image.filter(ImageFilter.GaussianBlur(0.4)))


def dumping(seed: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    rng = _rng(seed)
    image = Image.new("RGB", SIZE, (132, 128, 118))
    draw = ImageDraw.Draw(image)
    _road(draw, rng)
    for _ in range(46):  # a heap of debris
        x = rng.randint(140, 500)
        y = rng.randint(200, 380)
        size = rng.randint(18, 64)
        colour = rng.choice(
            [(120, 108, 92), (86, 96, 78), (150, 146, 138), (78, 70, 64), (168, 152, 120)]
        )
        draw.polygon(
            [
                (x, y),
                (x + size, y + rng.randint(-14, 14)),
                (x + rng.randint(4, size), y + size),
                (x - rng.randint(0, 18), y + rng.randint(10, size)),
            ],
            fill=colour,
        )
    _noise(image, draw, rng, 5200)
    return _encode(image.filter(ImageFilter.GaussianBlur(0.5)))


def uncollected_bin(seed: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    rng = _rng(seed)
    image = Image.new("RGB", SIZE, (152, 148, 140))
    draw = ImageDraw.Draw(image)
    width, height = SIZE
    for y in range(height):
        shade = 140 + int(30 * (y / height))
        draw.line([(0, y), (width, y)], fill=(shade, shade - 2, shade - 8))
    bx = rng.randint(200, 330)
    draw.rectangle([bx, 180, bx + 170, 420], fill=(52, 88, 62), outline=(32, 56, 40), width=4)  # bin
    draw.rectangle([bx - 10, 160, bx + 180, 190], fill=(44, 76, 54))  # lid, ajar
    for _ in range(26):  # bags spilling over
        x = bx + rng.randint(-60, 200)
        y = rng.randint(120, 200)
        size = rng.randint(26, 56)
        shade = rng.randint(40, 90)
        draw.ellipse([x, y, x + size, y + size - 8], fill=(shade, shade, shade + 6))
    _noise(image, draw, rng, 4000)
    return _encode(image.filter(ImageFilter.GaussianBlur(0.5)))


#: Taxonomy key → the drawing that depicts it. Keyed by the *same* keys the
#: seeder publishes as the tenant's taxonomy, so a category added there without
#: a picture here fails loudly rather than seeding blank evidence.
RENDERERS: Final[dict[str, object]] = {
    "roads.pothole": pothole,
    "roads.footpath": footpath,
    "water.leak": water_leak,
    "water.drain": open_drain,
    "light.out": street_light,
    "light.exposed": exposed_wiring,
    "waste.dumping": dumping,
    "waste.uncollected": uncollected_bin,
}


def render(node_key: str, seed: int) -> bytes:
    """The JPEG for one seeded report."""
    renderer = RENDERERS.get(node_key)
    if renderer is None:
        raise KeyError(f"no demo imagery for taxonomy node {node_key!r}")
    return renderer(seed)  # type: ignore[operator, no-any-return]


def _encode(image: object) -> bytes:
    buffer = io.BytesIO()
    # JPEG at 82: the format a phone actually produces, at a quality that leaves
    # compression artefacts the redaction stage has to cope with.
    image.save(buffer, format="JPEG", quality=82)  # type: ignore[attr-defined]
    return buffer.getvalue()
