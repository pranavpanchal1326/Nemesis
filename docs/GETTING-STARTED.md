# Getting Started

From a clean checkout to a running, verified stack. Every command is one word
via the task runner — `python tasks.py <task>` works identically if you prefer
not to use the wrapper.

## Prerequisites

| Requirement | Why |
|---|---|
| Docker Desktop (WSL2 backend on Windows) | The whole stack; the daemon must be running |
| Python 3.10+ on the host | Only to run `tasks.py`. The application itself is pinned to 3.12 **inside containers** — your host version is irrelevant to it |
| Ollama on the host | Serves the Investigation Agent's LLM with exclusive GPU access (ADR-0002) |
| ~8 GB free disk | Images ~6.8 GB + model weights ~3 GB |

**Windows: cap WSL2 first.** Docker's WSL2 backend will otherwise claim most of
your RAM and starve host-side Ollama. See `docs/HARDWARE.md` for the
`.wslconfig` to write. Skipping this on a 16 GB machine will cause thrashing.

## Bootstrap

```bash
cp .env.example .env
ollama pull llama3.1:8b
nem doctor
nem up
nem models
nem check
```

What each step does:

| Step | Result |
|---|---|
| `nem doctor` | Verifies Docker, git, `.env`, and Ollama reachability before you waste time on a build |
| `nem up` | Builds and starts six services, waiting until every healthcheck passes (~16 s once images are cached) |
| `nem models` | Fetches ~3 GB of weights into the `modelcache` volume and **executes each one**. Run once |
| `nem check` | Runs the full gate: lint, format, types, tests with coverage, migration state |

Expected end state: `86 passed`, coverage above 85%, all six services healthy.

## Verify the offline guarantee

The stack is designed to run with no internet (§6.6). Prove it rather than
assuming it:

```bash
nem models-verify
```

For the stronger test — no network interface at all — see the air-gap command in
`docs/MODELS.md`. All four cached models must pass; Ollama must fail, because it
is a host network service rather than a cached weight.

## Daily commands

```bash
nem check          # the same gate CI runs — run before every commit
nem test           # tests only
nem format         # apply ruff fixes and formatting
nem logs worker-ml # tail one service
nem psql           # open a shell on the database
nem help           # everything else
```

## Optional: pre-commit hooks

```bash
python -m pip install pre-commit
pre-commit install --install-hooks
pre-commit install --hook-type commit-msg
```

Fast checks only — secret scanning, whitespace, ruff, conventional commit
messages. Anything needing a running stack lives in `nem check` and CI, because
a slow hook is a hook everyone bypasses with `--no-verify`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `failed to connect to the docker API` | Docker Desktop is not running. Start it and wait for the engine to initialise |
| A service is `unhealthy` after `nem up` | `nem logs <service>`. Most often the container cannot reach Postgres or Redis — check `docker compose config` shows `redis://redis:6379`, not `localhost` (ADR-0005) |
| `/ready` returns 503 | Working as designed: a dependency is down. The body names which one |
| `nem models` fails on Ollama | The model is not pulled on the host: `ollama pull llama3.1:8b` |
| Tests fail with `no Postgres reachable` | `nem up` first; integration tests skip rather than fail when the datastore is absent |
| Everything is slow, host is swapping | WSL2 is uncapped. Write `.wslconfig` per `docs/HARDWARE.md`, then `wsl --shutdown` |

## Where to read next

| Document | Contents |
|---|---|
| `NEMESIS-Blueprint-v2.md` | The product design and its reasoning — the source of every § reference |
| `docs/PHASES.md` | The 30-phase program plan, tracks, owners, and exit gates |
| `docs/adr/` | Why the non-obvious decisions were made, and what would reopen them |
| `docs/HARDWARE.md` | The reference machine, resource budget, and how to scale off it |
| `docs/MODELS.md` | Model inventory, licensing, verification method, air-gap proof |
