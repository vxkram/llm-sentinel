# llm-sentinel

An LLM API gateway that sits in front of multiple LLM providers and enforces per-team rate limits and budgets, automatically retries and fails over to a backup provider when one is down, and gives full observability (traces, metrics, dashboards, alerts) into every request — all runnable for **$0**, no paid API keys required.

## Why this exists

Most portfolio projects that touch LLMs are "call an API, do something with the text." This one is the opposite: it's the piece of infrastructure a team puts *in front of* those calls once they have more than one provider, more than one team sharing a budget, and an on-call rotation that needs to know when a provider is degraded before users complain. Rate limiting, circuit breakers, distributed systems, observability tooling — the same problems as any production API gateway, applied to LLM traffic specifically.

## How it's $0

- **Ollama** is a real, local, free backend — the gateway makes genuine LLM calls against it.
- **OpenAI and Anthropic** are represented by real FastAPI servers that implement their *actual* wire formats (`/v1/chat/completions` with OpenAI-shaped chunks, `/v1/messages` with Anthropic's top-level `system` field and named SSE events) — not stubs that return canned JSON. The provider clients that talk to them (`OpenAIClient`, `AnthropicClient`) are genuine client code with a configurable base URL; pointing them at the real APIs instead is a one-line config change, not a rewrite.
- Each mock also exposes `POST /_admin/fault` for deterministic fault injection (`error`, `timeout`, `connection_reset`), which is how the circuit breaker and fallback routing below are actually tested and demoed — no need to wait for a real provider outage.

## What it does

- **Unified proxy** — one `/v1/chat/completions` endpoint across all three providers, streaming and non-streaming, with per-team system-prompt injection.
- **Rate limiting & budgets** — Redis Lua token buckets for RPM/TPM (atomic, race-free under concurrency), with a reserve-then-reconcile pattern for TPM since token counts aren't known until the LLM responds. Per-team daily/monthly USD budgets, enforced before the call goes out.
- **Resilience** — exponential-backoff retries with retryable/non-retryable error classification, a Redis-shared circuit breaker (CLOSED → OPEN → HALF_OPEN → CLOSED) so it stays correct across replicas, and tier-based fallback that always tries the requested model first and never routes to a model a team isn't allowed to use.
- **Observability** — OpenTelemetry spans across the full request lifecycle, a Prometheus registry (`/metrics`) covering requests/errors/latency/tokens/cost/fallback-rate/circuit-breaker state, 3 Grafana dashboards, and 4 real alert rules.
- **Admin API** — separately authenticated from team traffic, lets you inspect and change any team's limits/budget/allowed models live, no restart, with every change recorded to an audit log (Redis Stream + JSONL mirror).
- **Priority-aware concurrency** — `realtime` vs `batch` requests are gated through separate concurrency pools so a slow batch job can't starve interactive traffic.

## Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │                  llm-sentinel                 │
  client ──POST──▶  │  auth → budget check → rate limit → fallback  │
                    │        resolution → provider dispatch →       │
                    │        reconcile/charge → response            │
                    └───────┬───────────┬───────────┬───────────────┘
                            │           │           │
                      ┌─────▼───┐ ┌─────▼──────┐ ┌──▼───────────┐
                      │ Ollama  │ │ mock-openai │ │mock-anthropic│
                      │ (real)  │ │(real wire   │ │(real wire    │
                      │         │ │ format)     │ │ format)      │
                      └─────────┘ └─────────────┘ └──────────────┘
                            │
                      ┌─────▼────────────────────────────┐
                      │ Redis: rate-limit buckets, budget  │
                      │ counters, circuit-breaker state,   │
                      │ health history, audit stream       │
                      └─────────────────────────────────────┘
```

## Quickstart (local, no Docker)

This is how the whole project was actually built and verified — Docker isn't required.

**One command**, once the venv/Redis/Ollama prerequisites below are set up once:

```bash
./run.sh
```

Starts Redis, both mock providers, and the gateway; walks through every major
behavior (a real completion, rate limiting, automatic failover, budget
enforcement, a live admin change) with real requests and real output; then
tails the gateway log so you can keep poking at it. Ctrl+C stops everything.

Or step by step, if you'd rather run pieces individually:

```bash
# 1. Python 3.12 venv
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 2. Redis (installed via Homebrew, run ad hoc — not a login service)
redis-server --port 6379 --daemonize no &

# 3. Ollama, with a small local model pulled
ollama pull llama3.2:1b   # ollama serve should already be running

# 4. The two mock providers + the gateway itself
.venv/bin/uvicorn mock_providers.openai_mock.main:app --port 8011 &
.venv/bin/uvicorn mock_providers.anthropic_mock.main:app --port 8012 &
.venv/bin/uvicorn llm_sentinel.main:app --port 8010 &

# 5. Optional: populate some demo data so /metrics isn't empty
python scripts/seed_demo_data.py
```

Then:

```bash
curl -X POST http://localhost:8010/v1/chat/completions \
  -H "Authorization: Bearer sk-alpha-demo-000111" -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
```

```json
{"id":"chatcmpl-b913b59c0ab8","model":"gpt-4o-mini","provider":"openai","content":"[mock-openai] [system=You are Team Alpha's assistant. Always answer in a single short sentence.] You said: hi","usage":{"prompt_tokens":13,"completion_tokens":16,"total_tokens":29},"finish_reason":"stop","latency_ms":6.89}
```

## Quickstart (docker-compose)

```bash
docker compose up --build
```

Brings up the gateway, both mocks, Ollama, Redis, Jaeger, Prometheus, Alertmanager, and Grafana (`localhost:3000`) together — build-and-run verified: all 9 containers came up healthy, a real chat completion round-tripped through the gateway to Ollama, fault-injection/fallback/circuit-breaking behaved as described below, the live admin PATCH applied with no restart, Prometheus was scraping the gateway with all 4 alert rules loaded, and Grafana had its Prometheus datasource and all 3 dashboards auto-provisioned.

## See it actually work

**Automatic failover.** Fault-inject the mock to simulate an outage, then watch the gateway keep serving from a backup provider:

```bash
curl -X POST http://localhost:8011/_admin/fault -d '{"mode":"error","status_code":500,"rate":1.0}'
# ... after 3 failures, the circuit opens and requests transparently fall back:
curl -X POST http://localhost:8010/v1/chat/completions \
  -H "Authorization: Bearer sk-alpha-demo-000111" -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
```

```json
{"id":"ollama-1785687704538","model":"llama3.2","provider":"ollama","content":"I'm here to help with any questions or tasks for Team Alpha.","usage":{"prompt_tokens":41,"completion_tokens":15,"total_tokens":56},"finish_reason":"stop","latency_ms":1356.76}
```

Note `"model":"llama3.2"` — the client asked for `gpt-4o-mini`, but the response is honestly labeled with the model that actually served it.

**Rate limiting**, team-alpha's `rpm` is intentionally 3 for this demo:

```
req 1: 200   req 2: 200   req 3: 200   req 4: 429   (Retry-After: 12)
```

**Budget enforcement**, team-beta's daily budget is intentionally smaller than one real call:

```
call 1: 200 (succeeds, crosses the limit)   call 2: 402 "team 'team-beta' has exceeded its budget"
```

**Live admin changes, zero restart:**

```bash
curl -X PATCH http://localhost:8010/admin/teams/team-alpha \
  -H "X-Admin-Key: sk-admin-demo-999000" -d '{"rate_limit": {"rpm": 20}}'
# the very next burst of requests immediately reflects the new limit - no redeploy
```

## Load test

`tests/load/locustfile.py` runs two Locust user classes concurrently — one through the real gateway, one straight to the mock it proxies to — so a single run isolates the gateway's own added latency:

```bash
.venv/bin/locust -f tests/load/locustfile.py --headless -u 200 -r 50 -t 30s
```

| Concurrency | Gateway median | Direct-to-mock median | Gateway overhead | Throughput (gateway path) |
|---|---|---|---|---|
| 10 users | 9ms | 1ms | ~8ms | 502 req/s |
| 200 users | 230ms | 13ms | ~217ms | 355 req/s |

The first run of this test caught a real bug — every provider client was opening a brand-new `httpx.AsyncClient` per request instead of reusing a connection pool. Fixing that cut the 10-user overhead from 28ms to 8ms and roughly tripled throughput at the same concurrency. The overhead that remains at 200 concurrent users tracks with Locust's own CPU-saturation warning during that run: this machine is running Locust, the gateway, both mocks, and Ollama's background health prober all on the same CPU, which a real deployment wouldn't do.

## Testing

```bash
pytest                    # unit tests always run; integration tests need Redis
redis-server --port 6379 &
pytest                    # now the Redis-backed integration tests run too
```

Tests that need a live Redis connection live under `tests/integration/` and skip cleanly (not fail) when Redis isn't reachable, so `pytest` is green out of the box either way.

## Project layout

```
src/llm_sentinel/
  api/v1/           chat, admin, health/metrics endpoints
  providers/        unified schemas + Ollama/OpenAI/Anthropic clients + registry
  ratelimit/        Redis Lua token bucket
  budget/           cost calculation + budget tracking
  resilience/       retry, circuit breaker, fallback orchestration
  health_checker/   background provider health prober
  admin/            audit log
  priority/         realtime/batch concurrency semaphores
  observability/    Prometheus metrics + OpenTelemetry tracing
mock_providers/     real OpenAI/Anthropic wire-format mock servers
configs/            routing, pricing, per-team config (teams.yaml)
observability/      Prometheus/Grafana/Alertmanager config, dashboards
tests/{unit,integration,load}/
```

## Known limitations

- The load test was run against a single local uvicorn process, not a production multi-replica deployment.
- Circuit breaker thresholds, rate limits, and budgets in `configs/` are tuned for demoability, not production traffic patterns.

## License

MIT
