"""Phase 18's locale gate, against the running stack — A2.

    A locale added in the control plane appears in the UI with no code change.
        — §E25, Phase 18

**This is the one Phase 18 clause that cannot be checked without a running
stack, and therefore the one most likely to be assumed true.** The pieces have
all existed for a while and each looked finished on its own: `loadStrings`
merges base -> seed -> control plane, `/api/i18n/[namespace]/[locale]` proxies
the registry, and the control plane has had a translations importer since Phase
5. Nothing exercised the round trip, and two things were quietly missing:

* there was **no route to add a locale to a tenant that already exists**. The
  list is declared once at provisioning and nothing could change it, so the
  gate's first clause had no door to go through. `PUT /tenants/{slug}/locales`
  is that door (`control_plane/locales.py`).
* the frontend's language switch was **a two-element array in a component**, so
  a locale added upstream appeared in no switch and could not be reached. It is
  now built from what the tenant declares, published on every public body.

Both are code changes — made once, so that the *next* locale is not one.

The gate, executed in order against a live stack and a live frontend:

1. A locale nobody in this repository has heard of is **added over HTTP** to a
   tenant that already exists, with a justification, and lands on that tenant's
   chain as an ``admin_action``.
2. Its strings are **imported over HTTP** into the Phase 5 locale registry.
3. The rendered public page in that locale carries **the imported words**, not
   the source language's.
4. The language switch **offers** it, so a reader can reach it without being
   told the query parameter exists.
5. A key that was *not* translated still renders in the source language, which
   is the difference between a partially localised product and a broken one.
6. And the whole of the above happens with **no code change**: this script
   restarts nothing, rebuilds nothing and edits no file.

The locale is Konkani (``kok``) — a real language, official in Goa, for which
NEMESIS ships no copy at all. That is the point: a tag the repository has a
bundle for would prove the bundle works.

Standard library only. Exit code 0 clean, 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

API = os.environ.get("NEMESIS_API_URL", "http://localhost:8000")
WEB = os.environ.get("NEMESIS_WEB_URL", "http://localhost:3000")
CONTROL_PLANE = f"{API}/api/v1/control-plane"

TOKEN_HEADER = "X-Control-Plane-Token"
DEFAULT_TOKEN = "dev-only-insecure-control-plane-token-change-me"

OK, FAIL = "[ OK ]", "[FAIL]"

#: The tenant `nem seed-demo` provisions. It is the one this deployment's
#: frontend is configured for (`NEMESIS_TENANT_ID`), which matters: product-copy
#: strings resolve against the configured tenant, so adding a locale to some
#: other tenant would prove the API works and the UI does not.
DEFAULT_TENANT = "pune-demo"

#: Konkani. A real language, official in Goa, and one this repository ships no
#: bundle for — so every word that appears in it came through the registry.
LOCALE = "kok"

#: The namespace the words go into, and the key inside it.
#:
#: **`zone`, and not one of the UI namespaces, and that is the decision this
#: gate is written around.** `db/models/i18n.py` draws the line: *"Product copy
#: — button labels, error prose, the §22.1 consent text — is authored by
#: NEMESIS, versioned with the code, and reviewed like code… What lives in this
#: table is tenant-authored text… Mixing the two would mean a tenant could
#: overwrite the wording of a legal notice, which is not a localisation
#: feature."* So the registry carries `taxonomy`, `organisation`, `zone` and
#: `calendar`, and importing `public` is refused with a readable error.
#:
#: A ward's name is the clearest possible case of a string only the tenant can
#: be correct about, it renders above the fold on the city index, and it is the
#: half of the UI that Phase 18's *"no code change"* actually governs. Widening
#: the registry to swallow product copy so that a gate could assert a button
#: label would be relaxing an existing rule to make a new test pass — see
#: register row A17 for the part of this that is genuinely still open.
NAMESPACE = "zone"
ZONE_KEY = "W-KOTHRUD"

#: A sentinel rather than a real Konkani ward name. Real copy is a translation
#: nobody here can review, and this assertion is about the *path*, not about the
#: words — so the words say what they are. Stable across runs on purpose: a
#: `uuid` would make a re-run pass against the previous run's import.
SENTINEL = "कोंकणी वार्ड — via the control plane"


#: How long the public surface may take to show a change, in seconds. The
#: upstream `Cache-Control: public, max-age=300` plus a margin. Overridable,
#: because a deployment that has tuned `public_api.cache_seconds` has tuned this
#: number too and should not have to edit a gate to say so.
CACHE_WINDOW_SECONDS = int(os.environ.get("NEMESIS_PUBLIC_CACHE_SECONDS", "330"))


def _await_render(url: str, needle: str) -> str:
    """Poll until the cached page carries the change, or the window runs out."""
    deadline = time.monotonic() + CACHE_WINDOW_SECONDS
    html = ""
    announced = False
    while True:
        _, html = _get_text(url)
        if needle in html or time.monotonic() >= deadline:
            return html
        if not announced:
            print(
                f"  ....  waiting out the surface's cache window "
                f"({CACHE_WINDOW_SECONDS}s at most) - a transparency page is a "
                f"cached artefact by design"
            )
            announced = True
        time.sleep(5)


def _report(passed: bool, label: str, detail: str = "") -> bool:
    marker = OK if passed else FAIL
    stream = sys.stdout if passed else sys.stderr
    stream.write(f"  {marker} {label}{f' - {detail}' if detail else ''}\n")
    stream.flush()
    return passed


def _token() -> str:
    return os.environ.get("NEMESIS_CONTROL_PLANE_TOKEN", DEFAULT_TOKEN)


def _request(
    method: str, url: str, *, body: Any = None, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}


def _get_text(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        print(f"  {FAIL} {url} is not answering - {exc}", file=sys.stderr)
        return 0, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant", default=DEFAULT_TENANT, help="the publishing tenant to use"
    )
    parser.add_argument("--locale", default=LOCALE, help="the locale to add")
    args = parser.parse_args()

    tenant: str = args.tenant
    locale: str = args.locale
    admin = {TOKEN_HEADER: _token()}
    results: list[bool] = []

    print(f"\nPhase 18 locale gate - adding {locale!r} to {tenant!r} over HTTP\n")

    # ---------------------------------------------------------------- 0. up?
    status, _ = _get_text(f"{API}/health")
    if status != 200:
        print(
            f"  {FAIL} the API is not answering at {API} - start the stack",
            file=sys.stderr,
        )
        return 1
    # The control plane scopes writes by tenant *id* and the public surface is
    # keyed by *slug*; this is the one place the gate needs both. It is the same
    # value the frontend is configured with, and it has to be, or this script
    # would add a locale to one tenant and read a page rendered for another.
    tenant_id = os.environ.get("NEMESIS_TENANT_ID", "")
    if tenant_id == "":
        print(
            f"  {FAIL} NEMESIS_TENANT_ID is not set. It is the tenant the "
            "frontend is configured for, and the locale has to be added to "
            "that one - `nem seed-demo` prints it.",
            file=sys.stderr,
        )
        return 1

    status, _ = _get_text(f"{WEB}/{tenant}")
    if status == 0:
        print(
            f"  {FAIL} the frontend is not answering at {WEB} - start it with `nem web`,"
            " or point NEMESIS_WEB_URL at it",
            file=sys.stderr,
        )
        return 1
    if status == 404:
        print(
            f"  {FAIL} {tenant!r} does not publish - run `nem seed-demo` first",
            file=sys.stderr,
        )
        return 1

    # --------------------------------------- 1. the locale, added over HTTP
    status, body = _request(
        "PUT",
        f"{CONTROL_PLANE}/tenants/{tenant}/locales",
        headers=admin,
        body={
            # The whole list, stated. See `LocaleSpec` for why this is not an
            # increment: two operators running the same script must converge.
            "locales": ["en", "mr", "ar", locale],
            "justification": f"Phase 18 gate: proving {locale} reaches the UI with no deploy",
        },
    )
    declared = (body or {}).get("locales", []) if isinstance(body, dict) else []
    results.append(
        _report(
            status == 200 and locale in declared,
            "the locale is added to a tenant that already exists, over HTTP",
            f"{status} {declared}",
        )
    )

    # The `admin_action` this write appends to the tenant's chain is asserted by
    # the backend suite against a throwaway database, not here: the control
    # plane exposes no chain reader, and adding one so a gate script could look
    # would be building a disclosure surface to satisfy a test.

    # -------------------------------------- 2. its strings, added over HTTP
    status, body = _request(
        "PUT",
        f"{CONTROL_PLANE}/translations",
        headers={**admin, "X-Tenant-ID": tenant_id},
        body={
            "namespace": NAMESPACE,
            "locale": locale,
            "entries": {ZONE_KEY: SENTINEL},
        },
    )
    results.append(
        _report(
            status == 200,
            "its strings are imported into the Phase 5 locale registry, over HTTP",
            f"{status} {body}",
        )
    )

    # ---------------------------------------------- 3-5. the rendered page
    #
    # **The wait is not a workaround.** §26.4's surface is deliberately cached —
    # `Cache-Control: public, max-age=300` upstream, and the same window in the
    # BFF's fetches — because a transparency page is read by strangers and
    # rebuilding it per request is how a public page falls over the first time
    # somebody links to it. So a locale added now is visible when the window
    # turns over, and a gate that demanded it instantly would be asserting that
    # the cache is a bug. It waits the window out and says how long it waited.
    html = _await_render(f"{WEB}/{tenant}?locale={locale}", SENTINEL)

    results.append(
        _report(
            SENTINEL in html,
            "the imported words are on the page, with no code change and no deploy",
        )
    )
    results.append(
        _report(
            f'lang="{locale}"' in html.lower(),
            "the document declares the locale it negotiated",
        )
    )
    results.append(
        _report(
            # React preserves the camel case it was given (`hrefLang`), and HTML
            # attribute names are case-insensitive, so the browser is right and
            # a case-sensitive grep is not.
            f'hreflang="{locale}"' in html.lower(),
            "the language switch offers it, so a reader can reach it",
        )
    )

    # The untranslated wards must still be words. `localisation.py`'s fallback
    # is the row's own name rather than a chain, so a Konkani page with three
    # translated ward names and six English ones is a page in one language with
    # six gaps - which is honest - rather than a page of keys.
    results.append(
        _report(
            "⟦" not in html,
            "an untranslated name falls back to the row's own, not to its key",
        )
    )

    print()
    if all(results):
        print(
            f"\033[32m{OK} gate met - a locale added in the control plane appears in\n"
            "  the UI, asserted end to end against a live stack, with no code change\033[0m\n"
        )
        return 0
    print(f"\033[31m{FAIL} Phase 18's locale gate is not met\033[0m\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
