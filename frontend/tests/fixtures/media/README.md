# Test imagery

**Procedurally generated, not photographed.** `scripts/demo_imagery.py` draws
these from a fixed seed, so `pothole.jpg` is reproducible byte-for-byte:

```bash
python -c "import sys;sys.path.insert(0,'scripts');import demo_imagery as d,pathlib;pathlib.Path('frontend/tests/fixtures/media/pothole.jpg').write_bytes(d.render('roads.pothole',4242))"
```

## Why a real image and not four magic bytes

`tests/citizen.spec.ts` used to upload a JFIF header followed by 512 zero
bytes. That is enough for §25.1's magic-byte sniffer, which is all the _upload_
path looks at — and it is not enough for Phase 8, which **decodes** the file to
find faces. ADR-0032 is that a missing face detector halts the pipeline and
§22.1 fails closed, so an undecodable image is correctly refused, the trust
stage degrades, and the complaint parks at `pending_classification` having never
reached classification or dedup.

Which meant the M5 gate — _"every one of the six gates is driven by its real
event"_ — was asserting against a report the pipeline had legitimately stopped
processing. The fixture was testing the failure path while claiming to test the
success one.

## Why it is committed rather than generated in the test

Determinism, and the same argument `public/fonts/` makes: a gate that has to
run a Python script before it can assert is a gate that fails differently on a
machine without Pillow. The bytes are ours, they are reproducible from the
command above, and regenerating them is a deliberate act with a visible diff.

## What it is not

It is not a photograph of a street, and the classifier's opinion of it is
correspondingly weak — see `scripts/demo_imagery.py` for the full account. The
gates that depend on _classification confidence_ say so where they assert it.
