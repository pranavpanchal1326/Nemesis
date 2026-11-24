# Senior engineering audit — 2026-11-24

**Scope:** whole repository at `4f33198` (`main`). Everything below was *run*, not
read — except §6, which names what could not be run here and why.

**Summary.** The engineering culture in this repository is genuinely strong: ten
CI checkers, ten design guards, five generated-artefact gates, 589 frontend unit
tests and zero `TODO`s across ~450 modules all pass or hold. The problems are not
in the code it writes — they are in **four load-bearing gates that are currently
lying**, and in **one exposure that nothing in the repository discloses**.

| | Finding | Severity | State |
|---|---|---|---|
| F1 | All 95 Playwright golden baselines are corrupt bytes — CI is red on `main` | **P0** | **fixed**, see §13 |
| F2 | 4 critical / 30 high npm vulnerabilities, incl. Next.js unauthenticated RCE | **P0** | **fixed** |
| F3 | Dependabot has no `npm` ecosystem — the frontend tree is unmanaged | **P0** | **fixed** |
| F4 | The SBOM and vulnerability scan cover an image that nothing deploys | **P0** | **fixed** |
| F5 | `/console` has no authentication, and the BFF signs anonymous writes | **P1** | open |
| F6 | `.claude/` was not git-ignored — a 43 MB duplicate checkout, one `add -A` away | P1 | **fixed** |
| F7 | `check_env_parity.py` turns a parse failure into a false *security* finding | P2 | open |
| F8 | `.env.example` omits the SSRF control, and parity cannot see it | P2 | open |
| F9 | The local `.env` holds 12 of 25 keys — the stack cannot come up | P2 | open |
| F10 | `CHANGELOG.md` was stale (G9) — the generator had never been run | P3 | **fixed** |
| F11 | `docs/HANDOVER.md`, the stated entry point, is three months and a whole track stale | P3 | open |
| F12 | 8 orphaned corrupt JPEGs, referenced by nothing | P3 | **fixed** |
| F13 | `infra/postgres` has not been buildable for months — `nem up` exits 1 while reporting a healthy stack | **P0** | open, §14 |
| F14 | `.gitignore` listed two env filenames rather than the pattern | P2 | **fixed**, §15 |
| F15 | `nem web-golden` reused a stale install and ignored a failed one | P2 | **fixed**, §16 |
| F16 | The lockfile is valid only under the npm that wrote it — all three frontend CI jobs die at `npm ci` | **P0** | **fixed**, §17 |

§13 records what was changed to close the four P0s, including the one decision
in it that was larger than a patch bump.

---

## 1 · P0 — the visual regression gate is dead, and CI is red

Every one of the 95 PNG baselines under `frontend/tests/*-snapshots/` begins
`EF BF BD`. That is the UTF-8 replacement character, not a PNG signature — the
same text-codec round-trip that `4b31099` diagnosed for the image assets. That
commit said the snapshots "have to be re-recorded with `--update-snapshots`" and
noted it was not done. It still has not been done.

Verified directly:

```
Error: expect(locator).toHaveScreenshot(expected) failed
  Could not decode expected image as PNG.
  Snapshot: citizen-1-capture.png
```

**Blast radius.** Six `toHaveScreenshot` call sites, parameterised into 95
assertions across `golden.spec.ts` (84), `story.spec.ts` (10) and
`clay.spec.ts` (1). Those three specs carry the §E6.2 press-parity gate, the
Phase 19 WebGL2-vs-WebGPU golden, the §E24 density/script matrix and the §E18
suppression states — the assertions `playwright.config.ts` says "cannot be
asserted anywhere but in a real engine". All 95 fail before they test anything.

**Therefore the `frontend` CI job fails on every push to `main`**, because
`npm run test:e2e` is step 15 of it. Both platform sets (`-chromium-linux` and
`-chromium-win32`) are corrupt, so the Linux runner is no exception.

**Fix.** Regenerate on the platform each set is named for — a `-chromium-linux`
baseline recorded on Windows will never match, and recording it locally is how
this gate gets disabled a second time. Record the Linux set in CI (or in the
`mcr.microsoft.com/playwright` container), the win32 set locally, and commit
both. Then add a guard: a one-line PNG-signature assertion over
`tests/**/*.png` in `scripts/check-guards.ts` would have caught this the day it
happened, and would catch the next text round-trip too.

## 2 · P0 — 58 npm vulnerabilities, four of them critical

`npm audit` on the committed lockfile:

| Severity | Package | Direct? | Issue |
|---|---|---|---|
| **critical** | `next` 16.3.2 | direct | **Unauthenticated remote code execution on Windows-hosted servers** |
| **critical** | `maplibre-gl` | direct | XSS sanitiser bypass in `DOM.sanitize()` |
| **critical** | `storybook` | direct (dev) | Missing origin validation in the preview allows code execution |
| **critical** | `proxy-addr` | transitive | IP spoofing via IPv4-mapped IPv6 trust subnets |

Totals: **4 critical, 30 high, 21 moderate, 3 low**. `next` 16.4.6 and the
`maplibre-gl` fix are both available; `maplibre-gl` matters most in context
because it renders the *public* 2D tier, on pages a resident reaches without
authenticating.

## 3 · P0 — Dependabot does not watch the frontend

`.github/dependabot.yml` is thoughtfully written — it reasons about grouping, it
pins `torch` behind an ADR, and its header explains what it deliberately does
*not* cover. It configures three ecosystems: `pip` (`/backend`), `docker`
(`/backend`, `/infra/postgres`), and `github-actions` (`/`).

There is no `npm` entry. 215 frontend modules and their entire transitive tree
get no update automation at all, which is why F2 accumulated to four criticals
while seven Dependabot PRs sit open against the backend.

## 4 · P0 — the SBOM describes an image nobody deploys

The `supply-chain` job does exactly three things, all against one target:

```yaml
- run: docker build -t nemesis-api:ci --build-arg INSTALL_ML=false ./backend
- uses: anchore/sbom-action@v0   # image: nemesis-api:ci
- uses: anchore/scan-action@v5   # image: nemesis-api:ci, fail-build: true
```

Two consequences, and both undercut the artefact:

1. **`INSTALL_ML=false`.** The ML stack — `torch`, `mediapipe`, `open_clip`,
   `faster-whisper`, `scikit-*`, `pillow` — is the largest and most CVE-dense
   dependency set in the repository, and it ships in the `worker-ml` container
   that `docker-compose.yml` runs. It is in no SBOM and no scan.
2. **The frontend is never built here.** Nothing scans `package-lock.json`,
   which is how F2 went unreported by a job whose name is "SBOM &
   vulnerabilities".

So the published SBOM does not describe any image this system actually runs.
Scan `worker-ml` too (ML on, even if only on the weekly schedule, since the
build is slow), and add `npm audit --audit-level=high` or a Grype filesystem
scan of `frontend/` to the `frontend` job.

## 5 · P1 — the console has no authentication, and nothing says so

There is **no `middleware.ts`** anywhere in the frontend, no session, no login
and no role check. `frontend/src/app/(console)/console/page.tsx` guards nothing;
only the seven roadmap routes guard anything, and `devOnly()` is a build-mode
check, not an authorisation one.

Meanwhile `server/upstream.ts` holds `X-Control-Plane-Token` server-side and
attaches it to control-plane writes. That protects the secret from *theft* and
it is the right design — but combined with no session it means **the BFF signs
every anonymous visitor's write with the privileged token**. Anybody who can
reach the host can decide a review, activate a policy, change what a tenant
publishes, or provision a city. The review queue also renders citizen
photographs and the evidence behind each stopped gate.

The backend is honest about its half: `deployment.py` says a deployment keeping
the repository default "has no authentication at all", `SECRETS.md` says anyone
can mint a valid token, and `test_control_plane_api.py` notes it returns "403
rather than 401: there is no authentication scheme to challenge with". Phase 13
is the planned fix and `HANDOVER.md` correctly calls it the longest-lead blocker.

**The gap is disclosure, not ignorance.** No README line, no
`CONNECTING-A-BACKEND.md` line and no honesty-table row says *this console must
not be exposed to a network until Phase 13*. The honesty table labels console
screens `REAL`, the README invites `nem web`, and a reader is never told. For a
product whose entire premise is that it labels its own claims, that is the one
finding I would escalate above the four P0s on reputational grounds.

Two cheap mitigations before Phase 13: state the constraint in the README and
the honesty table, and make the API refuse to boot when `app_env != local`
without a configured auth scheme — the same shape as the existing `change-me`
refusal, which already proves the pattern works.

## 6 · P1 — `.claude/` was one `git add -A` from GitHub *(fixed)*

`.claude/worktrees/agitated-yalow-cadd74/` is a **43 MB full duplicate checkout**
of the repository, registered as a live `git worktree` on branch
`claude/agitated-yalow-cadd74` (26 commits, a superseded parallel lineage whose
tip duplicates `a567561` by title). `.claude/` was **not** in `.gitignore`, so a
single `git add -A` would have committed a second copy of the entire repository
*and* a Claude-named path to a public remote.

Fixed in this pass: `/.claude/` added to `.gitignore` with the reasoning. The
stale worktree and its branch are still on disk and should be removed —
`git worktree remove` then `git branch -D` — which also reclaims the 43 MB.

## 7 · P2 — a checker that degrades into a false security alarm

Run `scripts/check_env_parity.py` on Python 3.11 and it reports:

```
✗ local default for NEMESIS_JWT_SECRET appears outside config.py/deployment.py,
  in: control_plane/organisation.py, events/canonical.py, policy/resolver.py, worker/loop.py
```

**None of those four files contains that string.** The real cause is that all
four use PEP 695 syntax (`type JSONValue = ...`, `class Resolved[BodyT: PolicyBody]`),
which no interpreter below 3.12 can parse. On Python 3.14 all ten checkers pass
clean — the code is fine.

`check_tenant_scoping.py` and `check_media_redaction.py` are better: they at
least print `could not parse`. But they still report it **as a finding**, in the
same list and with the same exit code as a real violation, so an unparseable
file looks like a tenancy breach.

A checker that silently converts a tooling failure into a domain violation is
the precise failure mode rule 7 — *prove, don't log* — exists to prevent, and
the inverse (a parse failure counted as a *pass*) would be worse still. Fix:
assert `sys.version_info >= (3, 12)` at entry in every checker, and exit with a
distinct non-finding error code on `SyntaxError`.

## 8 · P2 — the SSRF control is not in `.env.example`

`NEMESIS_WEBHOOKS__ALLOW_PRIVATE_NETWORK_TARGETS` is a real setting
(`config.py:373`), it is the SSRF control for webhook delivery, and
`config.py` uses `extra="forbid"` so every key matters. It is present in the
working `.env` and **absent from `.env.example`** — so an operator who copies
the example, as every document tells them to, never learns the control exists.

`check_env_parity.py` passes anyway, because it compares `.env.example` against
the deployment-contract list in `deployment.py`. A security-relevant setting
outside that list is invisible to it. Either add it to the contract or widen the
checker to assert that every `Settings` field with a security consequence is
represented in the example.

## 9 · P2 — the local `.env` cannot start the stack

It holds **12 of the 25 keys** in `.env.example`. Missing, among others:

- `POSTGRES_PASSWORD` — compose interpolates it into both the Postgres container
  and `NEMESIS_DATABASE_URL`. Unset, the official image refuses to initialise.
- `NEMESIS_CONTROL_PLANE_TOKEN`, `NEMESIS_WEBHOOK_SIGNING_KEY` — fall back to
  the insecure code defaults, which is survivable at `app_env=local` and fatal
  at `pilot`.
- `NEMESIS_CORS_ALLOW_ORIGINS`, `NEMESIS_RATE_LIMIT__*`, `NEMESIS_OTEL__*`,
  `NEMESIS_FLAGS__*`, `NEMESIS_INGEST__MAX_*`.

This is exactly the trap `HANDOVER.md` §5 warns about — *"copy the whole file
rather than the three lines somebody quoted in a message"* — and it is the most
likely reason the Docker stack is not running on this machine. Fix:
`cp .env.example .env` and re-set the four local values.

## 10 · P3 — `CHANGELOG.md` was stale *(fixed)*

Gap **G9** in the handover. The `[Unreleased]` section listed only README and
logo commits; Phases 2 through 10, the whole frontend, and the asset recovery
were all absent from a document whose own header says *"a changelog that is
wrong is worse than none: during an incident it is read as a record."*

The cause was mundane — `scripts/generate_changelog.py` had never been run.
Running it once added all 20 missing entries. **Worth a pre-push hook or a CI
check**, since the fix is a single command and the gap persisted nine phases.

It also prints: *"2 commit(s) did not parse as conventional commits … If this
number is not zero, the commit-msg hook is being bypassed."* The two are the
GitHub-authored merge commits — the web UI does not run local hooks, so the
message is accurate and the hook is bypassable by design. Worth either a CI
check on commit messages or a line in the generator acknowledging merge commits.

## 11 · P3 — the stated entry point is badly stale

`docs/HANDOVER.md` opens *"This document is the entry point. Read it once, end to
end, before writing any code"* and then describes a different repository:

| It says | Actually |
|---|---|
| Status date 2026-08-24, commit `02df9b7` | three months and 11 commits ago |
| 36 ADRs | **61** |
| 27 runbooks | **28** |
| 172 source modules | **179** |
| "Phases 18–22 ❌ not started", "the entire frontend" not built | the frontend is complete, gated and photographed in the README |

Rule 9's own argument applies to this file: a wrong record read during an
incident sends the investigation the wrong way. The §7 carried-forward-gaps
table (G1–G9) is still accurate and valuable; the status header, the phase table
and the health metrics need a refresh, and G9 can be struck.

## 12 · P3 — eight orphaned corrupt images

`assets/` and `docs/images/` each hold `landing-map.jpg`,
`portal-residents.jpg`, `portal-staff.jpg` and `receipts.jpg` — all eight
beginning `EF BF BD`, all eight listed in `4b31099` as having no intact
ancestor. Since the README moved to `assets/screens/`, **nothing references
any of them.** 1.9 MB of broken bytes pretending to be product captures.
Delete them, or re-shoot from `capture-product-shots.ts`.

---

## What is genuinely clean

Stated because an audit that only lists faults misrepresents the repository.

- **All ten CI checker scripts pass** on a correct interpreter: runbooks, env
  parity, phase coverage, surface traceability, memory budget, observability,
  tenant scoping, event catalog, domain literals, media redaction.
- **Every frontend gate passes**: `tokens:check`, `honesty:check`,
  `camera:check`, `api:types:check`, `fonts:check` (20 families, 51 files),
  the 10 design guards, `tsc --noEmit`, `eslint`, `prettier --check`, and
  **589 unit tests in 34 files**.
- **Zero `TODO` / `FIXME` / `HACK` / `XXX`** across ~450 source modules. Debts
  live in `HANDOVER.md` and `BACKLOG.md` as numbered, owned items instead.
- **No build artefact is tracked**; `.env` has never been committed in any
  commit on any branch; a `gitleaks` job runs over full history.
- **The README's numbers verify against the tree**: 179 backend modules /
  48,241 lines, 215 frontend modules, 34 registered event types, 61 ADRs,
  28 runbooks. The honesty discipline survives being checked.
- **The `devOnly()` 404 guard is real.** The seven unwired console routes call
  it as their first statement, and `lib/dev-only.ts` is a genuine
  `notFound()` on `NODE_ENV === "production"`.

## What could not be verified here

- **The backend suite** (1186 tests), the 85 % coverage floor, `ruff`,
  `mypy --strict`, Alembic reversibility and all ten phase gates. The Docker
  daemon is down (see F9) and no 3.12 interpreter exists on this machine —
  `pyproject.toml` pins `>=3.12,<3.13`, and the local installs are 3.11, 3.13
  and 3.14. Nothing here suggests they fail; they simply were not run.
- **The live-stack Playwright specs** (`citizen.spec.ts`, `console-real.spec.ts`,
  `contracts.spec.ts`) fail locally for want of the backend, not for want of
  correctness. Their failures are excluded from F1, which rests only on the
  undecodable baselines.
- **Lighthouse budgets and the Storybook diff**, both of which need a
  production build and a base-branch build respectively.

---

## 13 · What was changed to close the four P0s

Recorded here rather than in a commit message because two of these were
decisions rather than bumps, and a decision needs its reasoning next to the
measurement that forced it.

### F2 · The dependency tree

| | before | after |
|---|---|---|
| critical | 4 | **0** |
| high | 30 | **0** |
| moderate | 21 | 12 |
| low | 3 | 0 |

The production tree (`--omit=dev`) is **0 critical, 0 high, 7 moderate**.

- `next` **16.3.2 → 16.4.6** — the unauthenticated RCE. The exact pin is kept;
  this package and `typescript` are the only two the repository pins without a
  caret, and that was deliberate.
- `maplibre-gl` **5.24.0 → 6.19.0 — a MAJOR, and the one decision here that
  was larger than a patch.** The advisory's affected range is `<=6.4.0`, so the
  5.x line is abandoned *with the XSS sanitiser bypass unfixed*: 5.24.1 is the
  last 5.x and it is vulnerable. There is no patch release to take, and the
  surface is public resident-facing pages, so the choice was a major bump or a
  live XSS. Blast radius checked before taking it: MapLibre is imported in
  **exactly one file**, `src/clay/FlatMap.tsx`, and only through
  `new Map({...})`, `getCenter`, `getZoom`, `getBearing`, `getPitch`,
  `getCanvas`, `on`, `off` and `remove` — the most stable corner of that API,
  and no style, source, layer or control API beyond a single background paint.
- `deck.gl` 9.3.10 → 9.4.1, `@lhci/cli` 0.15.1 → 0.16.1 (every remaining high
  lived in lhci's puppeteer/proxy-agent tree), plus non-breaking `npm audit fix`.
- One `overrides` entry, `image-size: ^2.1.0`, reasoned in `package.json`. npm's
  own suggested fix for that advisory was to **downgrade `deck.gl` 9.4.1 → 9.1.0**,
  a major step backwards past the version §E20's 2D tier is written against.
  Declined.

**Verified after all of it:** the five generated-artefact gates, the 10 design
guards, `tsc --noEmit`, `eslint`, `prettier --check`, **589/589 unit tests**, and
a clean `next build` across all 30 routes.

**One thing that had to be decided rather than suppressed.** `npm install`
drifted `typescript-eslint` 8.67.0 → 8.74.0 *within its own declared caret
range*, and a rule new in 8.7x — `no-unsafe-class-instance-assignment` — began
firing on `clay/clay-tsl.ts:222` and `clay/picking.ts:69`. It is a false
positive that **no correct TSL program can satisfy**: three.js types
`material.colorNode` and `material.normalNode` as instances of the `Node`
class, while TSL's entire public API is factory functions (`vec4()`,
`normalMap()`, `texture()`) returning node proxies deliberately not built with
`new`. Satisfying it would mean not using TSL, which ADR-0037 chose.

Turned off in `eslint.config.ts` for `src/clay/**` and `src/press/**` only —
the two directories that hold node graphs — as a property of the shading
language rather than a concession about two call sites. Two inline disables
would have been reopened by the third TSL assignment, and a global disable
would lose a useful rule across the other 213 modules.

### F3 · Dependabot

An `npm` ecosystem for `/frontend`, grouped by what breaks together rather than
what is tidy:

- **`three-stack`** — `three`, `@react-three/*`, `three-mesh-bvh`,
  `@types/three`. drei pins a fiber range, fiber pins a three range, and
  `@types/three` tracks three's minor exactly — which is the minor ADR-0037's
  TSL shaders are written against. One of these alone is unevaluable.
- **`next-stack`** — React 19's types are a peer requirement of Next 16, so a
  lone `@types/react` bump is a typecheck failure wearing a patch version.
- **`geo-stack`**, **`motion-stack`**, **`dev-tooling`**.
- TypeScript majors ignored, pointing at ADR-0042.

### F4 · The supply chain

`supply-chain` is now a two-leg matrix over **both** images the repository
deploys — the API build and `worker-ml` with `INSTALL_ML=true` — so torch,
mediapipe, open_clip and faster-whisper are finally inside an SBOM.
`fail-fast: false`, because one image failing must not cancel the scan that
would have reported the other. Each leg uploads SARIF under its own
`category:`; without that the matrix legs silently overwrite each other and the
security tab describes one image while appearing to describe both.

A new `supply-chain-frontend` job SBOMs and audits the npm tree. The gate is
`npm audit --omit=dev --audit-level=high`; the Grype filesystem scan beside it
is **deliberately not** a gate (`fail-build: false`) because it reads a
different advisory database and has no concept of a devDependency, so gating on
it would gate releases on dev tooling *and* on two databases agreeing. The
full-tree audit reports and does not fail, for the same reason: a devDependency
CVE cannot reach a citizen, and blocking a release on one is how a team learns
to pass `--force`.

### F1 · The baselines, and the guard that comes first

`scripts/check_binary_integrity.py` was written **before** re-recording
anything, because the re-record is the dangerous half: it accepts whatever
today's build draws as the new truth, and the images it replaces have no intact
ancestor anywhere in history, so there is nothing to diff the new set against.

The checker verifies magic bytes for 15 formats across `frontend/tests`,
`frontend/public` and `assets`, names the U+FFFD text round-trip explicitly when
it sees it, treats a zero-byte file as a failure, and — the clause that matters
most — **fails when it finds nothing to check**, because every search root
being absent would otherwise be indistinguishable from a pass and would stay
green through any amount of damage. Wired into `nem check` and the CI
`governance` job. It found exactly 103 of 243 files on first run.

Its failure message carries what the original incident taught: *a Playwright
baseline reports this as a failing visual assertion, not as a broken file — do
not re-record a baseline to make this pass without first confirming the image it
replaces was a correct one.*

**`--update-snapshots` does not repair a corrupt baseline, and `4b31099` was
wrong about this.** That commit recorded the remedy as *"the snapshots have to
be re-recorded with `--update-snapshots`"*. Run exactly that and it writes
nothing: the flag's preset mode is `changed`, and an undecodable file raises an
error rather than producing a diff, so it never qualifies as changed. The run
exits **0**, reports passes, and leaves all 95 corrupt files in place — a third
silent failure stacked on the first two. The flag has to be `-u all`, which
rewrites unconditionally, or the files have to be deleted first so that
`missing` applies.

This matters beyond the fix: anyone following the instruction left in the
repository would have concluded the problem was elsewhere.

**The platform split is structural, not incidental.** Playwright names baselines
`-chromium-linux` and `-chromium-win32` because font rasterisation and GPU
rendering genuinely differ, and CI compares against the Linux set. Dropping
`{platform}` from `snapshotPathTemplate` to halve the work would make CI compare
Linux renders against Windows baselines and fail permanently — so both sets are
recorded on the platform they are named for, the Linux set inside
`mcr.microsoft.com/playwright:v1.62.1-noble` with an anonymous volume over
`node_modules` so the container's Linux binaries never overwrite the host's.

**The `nem web-golden` task only ever drove one spec, and that is why the host
captures exist.** It named `tests/golden.spec.ts` alone — correct when that was
the only spec with baselines. `story.spec.ts` (ten acts) and `clay.spec.ts` (the
WebGL2 golden) grew baselines later, at `b7e6b70`, and were never added. So
**eleven of the ninety-five committed PNGs had no sanctioned capture path at
all**, and the only route left to whoever needed them was a host run — the one
thing both that docstring and `golden.spec.ts`'s own header forbid. The
`-chromium-win32` set is best read as the symptom of a tool that did not cover
the files somebody had to produce, not as carelessness.

Fixed by discovery rather than by adding two more hardcoded paths: the task now
drives every spec under `tests/` that asserts `toHaveScreenshot`, so a spec that
grows a baseline is covered the day it does.

**Measured result.** 42 Linux golden baselines captured in the container against
the live stack, every one a valid PNG, and the verification re-run is
**46/46 passed, exit 0** — comparing at `maxDiffPixels: 0`. The gate is green on
the platform CI uses, for the first time since the corruption.

**The 53 win32 baselines are deleted, and the hole they came through is closed.**
All 53 were corrupt and unusable as baselines regardless of policy, so nothing
was lost. Deleting them alone would not have been a fix, though: Playwright's
default mode for an absent baseline is `missing`, which **writes** the
screenshot and fails that test — so the next `npm run test:e2e` on a Windows
machine would have silently recreated all 53 as *valid* PNGs that
`check_binary_integrity.py` would happily pass. The files were the symptom.

The cause was that the rule existed only in prose. `playwright.config.ts` now
carries `ignoreSnapshots: process.platform !== "linux"`, which skips **only**
the snapshot expectations off-Linux. Every other assertion in those three specs
— the axe sweeps, ADR-0038's text-exemption check, the
stamps-come-from-the-ledger tests — still runs on a developer's machine, where
their value is. The image comparison waits for the platform CI uses.

This was decided rather than asked, for two reasons worth stating: the new
integrity gate would have held CI red for as long as 53 corrupt files existed,
and re-recording them on the host was the one option both source documents
forbid. **The consequence to be aware of:** a Windows `npm run check` no longer
exercises the 95 image comparisons at all. It reports them as passed because
they did not run. If that silence is unacceptable, the answer is a line in
`check` that says so out loud, not a return to host baselines.

**What re-recording accepts, stated plainly.** The clay and map snapshots are now
recorded against MapLibre 6 and deck.gl 9.4.1 rather than the versions the lost
originals were drawn with. The grounds for trusting the new set are that the
build passes every other gate — not that the images were compared with what they
replaced, because that comparison is no longer possible.

### F12 · The orphans

Eight JPEGs under `assets/` and `docs/images/` — `landing-map`,
`portal-residents`, `portal-staff`, `receipts` — deleted. `git rev-list --all`
confirms no intact ancestor blob exists for any of them, nothing in the
repository references them, and the 76 intact captures in `assets/screens/`
superseded them at `fc312ca`. `docs/images/` is now empty and removed.

---

## 14 · F13 — the datastore image has not been buildable for months *(found while fixing F1)*

**P0, and it was invisible because the machine that found it already had the
image.** `nem up` reports a successful stack — eight services, all healthy — and
exits **1**. The eight healthy containers are real; the postgres one is running
a locally cached image built **three months ago**, and `infra/postgres/Dockerfile`
can no longer produce it:

```
target postgres: failed to solve: process "/bin/sh -c apt-get update
  && apt-get install -y --no-install-recommends postgresql-17-pgvector
  && rm -rf /var/lib/apt/lists/*" did not complete successfully: exit code: 100
```

Diagnosed against the base image directly, so this is not a network flake:

```
$ docker run --rm postgis/postgis:17-3.5 \
    bash -lc "apt-get update; apt-cache policy postgresql-17-pgvector; \
              apt-get install -y postgresql-17-pgvector"
E: Unable to locate package postgresql-17-pgvector
```

`apt-cache policy` returns **nothing at all** — the package is not merely the
wrong version, it is absent from the base image's apt sources. `postgis/postgis:17-3.5`
is a **mutable tag**: it moved under this repository (the digest pulled today is
`sha256:01a6a70e…`), and whatever base or repository set it now carries no
longer includes PGDG's pgvector package.

**Why this is P0 rather than an inconvenience.** pgvector is not optional here —
ADR-0001 puts relational, geospatial and vector in one Postgres, and ADR-0012
stores image embeddings as `halfvec`, which is a pgvector type. A database
without the extension cannot host the columns the perception layer writes.

**What it breaks that nobody has seen yet:**

- `nem up` on any fresh machine. This is step two of the README's quick start,
  so the first thing a new engineer runs is the thing that fails.
- CI's `stack · boot & probes` job, which runs `docker compose up` on a clean
  runner with no cache. It has presumably been failing alongside the `frontend`
  job (F1), which is how two red jobs hide each other.
- Any rebuild for a security patch. The running image is three months of
  Postgres and PostGIS CVEs behind and cannot currently be refreshed.

**Not fixed here, because the fix is a decision.** Four options, and this
repository's own standards point at different ones:

1. **Pin the base by digest.** Restores reproducibility in one line and is the
   most faithful to the pinning discipline elsewhere — but it freezes the
   datastore at today's CVE set, which is the trade `0002`-style pins make
   explicitly and with an ADR.
2. **Add the PGDG repository explicitly**, then install the package. Removes the
   dependency on whatever the base image happens to ship, which is the actual
   root cause.
3. **Build pgvector from source** in the image. No repository dependency at all,
   at the cost of a compiler in the build stage.
4. **Base on `pgvector/pgvector:pg17`** and add PostGIS instead of the reverse.

(2) or (3) addresses the cause; (1) only stops the bleeding. Either way the
mutable tag should become a digest, and ADR-0001 is the document that owns the
consequence. **This needs an ADR and a runbook entry, not a quiet one-line
patch** — and it is the one finding in this report that stops a new engineer at
the second command in the README.

---

## 15 · F14 — `.gitignore` covered two env filenames, not the pattern *(small, fixed)*

The secrets block listed `.env` and `.env.local` as exact names. Covered.
`.env.production`, `.env.staging`, and — the one that actually happened — a
`.env.bak-<timestamp>` written while repairing F9 were **not**, and a backup of
a populated `.env` is the same secrets under a name nothing was watching.

Now `.env` plus `.env.*` with `!.env.example` exempted, verified in both
directions: every variant above is ignored, and both tracked `.env.example`
files (root and `frontend/`) are still tracked — the root one has to be, because
`nem parity` reads it.

Worth noting what did *not* go wrong: `git log --all -- .env` is empty, so no
env file has ever been committed on any branch, and the `secrets` CI job runs
gitleaks over full history. This was a gap in front of a door that had not been
opened.

---

## 16 · F15 — `nem web-golden` reused a stale install and ignored a failed one *(fixed)*

Found by it breaking the F1 capture, and it is the fourth
failure-that-reports-success in this report.

The container step was:

```sh
[ -x node_modules/.bin/playwright ] || npm ci --no-audit --no-fund; npx playwright test …
```

Two defects, compounding:

1. **The guard asks the wrong question.** It checks whether playwright is
   present, not whether the install matches `package-lock.json`. The named
   volume `nemesis-golden-node-modules` is therefore populated once and reused
   forever. After the F2 dependency bumps it still held the old tree, so
   `deck.gl`, `maplibre-gl` and `three-mesh-bvh` were absent.
2. **`;` rather than `&&`.** A failed or partial `npm ci` falls straight through
   into the test run.

**What that looks like when it happens, which is the worst part.** Next reports
it as a *source* error in a file nobody has touched:

```
⨯ ./src/clay/FlatMap.tsx:87:9
Error: Module not found: Can't resolve 'deck.gl'
```

Nothing says "your install is stale". The clay and story pages fail to compile,
their assertions fail on a visibly empty element with
`expect(locator).toBeVisible() failed`, and **the baselines they would have
written are silently not written** — so the run looks like eleven ordinary test
failures in the 3D code, in the middle of a task whose whole purpose is writing
those eleven files. The first instinct is to go and debug `FlatMap.tsx`.

**Fixed** by stamping the lockfile's sha256 beside the install, comparing it on
each run, and `|| exit 1` on the install so a partial tree can never reach the
test run.

### The pattern worth naming

Five findings in this report share one shape — **a failure that reports
success**:

| | What reported success |
|---|---|
| F1 | A corrupt baseline reports as a *failing visual assertion*, indistinguishable from a renderer regression |
| F1 | `--update-snapshots` exits **0**, says "31 passed", and writes nothing |
| F13 | `nem up` prints eight healthy services and exits **1** |
| F15 | A stale install reports as `Module not found` in untouched source |
| F7 | A checker that cannot parse a file reports a *security violation* |

This repository's rule 7 is **"prove, don't log"** and its gates are unusually
good. The gap is not in what it measures, it is that several of its tools
**cannot distinguish their own breakage from the thing they measure**. That is
the class of bug to hunt next, and the cheapest general defence is the one
`check_binary_integrity.py` adopts: fail loudly when you find nothing to check,
and never let a tooling error take the same exit path as a finding.

---

## 17 · F16 — the lockfile is only valid under the npm that wrote it, so **every frontend CI job dies at `npm ci`** *(fixed)*

**This is the most consequential finding in this report, it predates every
change made today, and nothing in the repository could have revealed it**
— because it reproduces only on a clean Linux install with a current npm, which
is exactly what CI does and exactly what no developer does.

Measured, against the **committed** `HEAD` lockfile, in CI's own container
image, changing nothing but the npm version:

| npm | `npm ci` |
|---|---|
| **11.6.2** — the version that wrote the lock, and the host's | ✅ `added 1191 packages in 8s` |
| **11.16.0** — what a fresh Node 24 ships today | ❌ `Missing: @emnapi/runtime@1.11.3 from lock file` |

The missing nodes are `@emnapi/runtime` and `@emnapi/core`, optional
native-binding helpers reached through `sharp`'s wasm fallback. A newer npm
resolves that optional subtree differently and demands lock entries the older
npm never recorded. The lockfile is not corrupt — **it is valid only under the
npm that generated it.**

**What that costs.** `ci.yml` installs node with
`actions/setup-node@v4`, `node-version: "24"`, which brings whatever npm ships
with the current Node 24 release. That has drifted past 11.6.2, so today
`npm ci` fails — and `npm ci` is step one of **all three** frontend jobs:
`frontend`, `lighthouse` and `storybook-diff`. None of them has reached a test,
a build, a Lighthouse budget or a Storybook diff. **F1's 95 corrupt baselines
were never even the reason the frontend job was red; the job was dying two
minutes earlier.** Two independent red causes, each hiding the other.

And note the shape: **nothing in this repository changed.** The environment
moved underneath a pinned-looking file. `node-version: "24"` reads as a pin and
is not one — it floats the npm that enforces every other pin.

**Fixed** by regenerating the lock under the newer npm, inside the container,
then verifying it in both directions — the one check the original lock could
never have passed:

| | `npm ci --dry-run` |
|---|---|
| container, npm 11.16.0 | ✅ `added 1188 packages` |
| host, npm 11.6.2 | ✅ exit 0 |

Dependency versions are unchanged by the regeneration (`next` 16.4.6,
`maplibre-gl` 6.19.0, `image-size` 2.1.0 via the override), and the host's
gates still pass afterwards: `tsc --noEmit` clean, **589/589** unit tests.

**A trap worth knowing before you run anything.** The regeneration is undone by
**any `npm install` on the host**. Measured: after regenerating in the container
the lock carries a `node_modules/@emnapi/runtime` entry; one host
`npm install` — which reports a cheerful `up to date in 7s` — removes it again,
and the next CI run fails exactly as before. So the fix has to be applied in the
container and then left alone, and an ordinary local `npm install` is enough to
silently re-break CI.

That moves the pin below from *desirable* to *required*: without it this
finding returns the first time anybody installs a package on a developer
machine, and the only symptom will be CI failing on a commit that did not touch
the frontend.

**Still owed, and it is the real fix.** Regenerating the lock cures today's
symptom; it does not stop the next npm release doing this again. npm is the only
tool in this repository's toolchain that is **not** pinned — Python is
`>=3.12,<3.13`, the Playwright image is derived from `package.json`, torch is
pinned behind an ADR, actions are pinned by Dependabot. The options are a
`packageManager` field with corepack, or an explicit `npm i -g npm@<version>`
step before each `npm ci` in CI. That is a one-line change with a workflow
consequence for every developer, so it belongs in an ADR rather than in this
pass — but until it lands, this finding has a return date and no alarm attached.
