# Ollama / LLM service unreachable

- **Severity:** warning — this is a *designed* degradation, not an outage
- **Owner:** DATA (capability) · SRE (host service)
- **Alerts:** `NemesisOllamaUnreachable`, `NemesisAgentInvestigationBudgetBreached`

**Dependency:** ollama

**Blueprint scenario:** Ollama/LLM service unreachable

> Read this first: **nothing is broken that requires urgency.** §27.3 specifies
> that ambiguous cases route to the human review queue when the LLM is
> unavailable, and classification does not depend on Ollama at all. The cost of
> this failure is human time in the review queue, not lost or mishandled
> complaints. Treat it accordingly — and resist the reflex to restart things.

## Symptoms

- `nemesis_system_degradation_total{dependency="ollama"}` rising.
- Review queue depth growing faster than usual.
- `agent_investigation` stage duration at or beyond its 90 s budget, or absent
  entirely because invocations are failing before they start.
- Log events `system_degradation` with `dependency="ollama"` and a reason of
  `connect_timeout`, `request_timeout`, or `model_not_found`.

## How to confirm

From the host (Ollama runs on Windows with the GPU — ADR-0002 — so this is
never checked from inside a container):

```bash
curl -s --max-time 5 http://localhost:11434/api/tags
```

Then confirm the containers can reach it, which is a different question and the
one that fails more often:

```bash
docker compose exec api python -c "import urllib.request;print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags',timeout=5).status)"
```

If the first succeeds and the second fails, this is a networking problem
(`host.docker.internal`, `extra_hosts`, WSL2 networking mode), not an Ollama
problem. Do not restart Ollama.

## Immediate mitigation

1. **Confirm the fallback is actually engaged** before touching anything:

   ```bash
   curl -s localhost:8000/ops/flags | grep -A2 pipeline_agent_investigation
   ```

2. If the degradation is intermittent — some invocations succeeding, most
   timing out — that is worse than a clean failure, because each attempt holds a
   worker for the full 90 s timeout before falling back. Pull the handle to make
   the failure clean and fast:

   ```bash
   nem flag kill pipeline_agent_investigation --actor "$USER" --reason "ollama flapping, incident <id>"
   ```

   Every ambiguous case now goes straight to human review with no 90 s wait.
   This is the same path §27.3 specifies, entered deliberately.

3. Restart the host service if it is genuinely down:

   ```powershell
   Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process
   ollama serve
   ```

4. Confirm the model is present — a pulled-then-deleted model presents exactly
   like an unreachable service:

   ```bash
   ollama list | grep llama3.1:8b
   ```

5. When healthy again, restore the flag. **Do not leave the kill switch
   pulled** — a permanently killed agent is a capability quietly removed from
   the product with no decision recorded:

   ```bash
   nem flag clear pipeline_agent_investigation --actor "$USER" --reason "ollama recovered"
   ```

## Root cause investigation

Ordered by observed frequency on this hardware:

1. **VRAM exhaustion.** The RTX 5060 has 8 GB and `llama3.1:8b` uses ~5.5 GB
   under context. A browser WebGL context sharing the card can push it over —
   which is why Phase 19 budgets scene VRAM at ≤ 512 MB and asserts it in CI.
   Check with `nvidia-smi`.
2. **The host went to sleep.** Laptop, battery, demo. Ollama does not always
   recover its listener cleanly.
3. **`host.docker.internal` did not resolve.** Requires `extra_hosts:
   host.docker.internal:host-gateway`, which is set on every backend service —
   confirm it survived any compose edit.
4. **WSL2 networking mode changed.** `networkingMode=mirrored` in `.wslconfig`
   changes how the host gateway resolves.

## Prevention

- Phase 16's gate requires Ollama-down to degrade to human review with a
  `system_degradation` event and never to hang. That gate is the durable fix;
  this runbook covers the window before it lands.
- Phase 25 exercises this scenario as an automated `toxiproxy` test, at which
  point "we think the fallback works" becomes "the fallback is verified on every
  commit".
- Demo-day pre-flight (§27.4) includes an Ollama reachability check for exactly
  this reason.
