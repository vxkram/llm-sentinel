#!/usr/bin/env bash
# Starts the whole local stack (Redis, both mock providers, the gateway)
# and walks through every major feature with real requests, so you can see
# it actually work end to end. Ctrl+C stops everything cleanly.
#
# Requires: a Python venv already set up (.venv/bin/pip install -e ".[dev]"),
# Redis installed (brew install redis), and Ollama running with llama3.2:1b
# pulled. See README.md if any of those aren't set up yet.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_PY=".venv/bin/python"
VENV_UVICORN=".venv/bin/uvicorn"

GATEWAY_PORT=8010
OPENAI_MOCK_PORT=8011
ANTHROPIC_MOCK_PORT=8012
REDIS_PORT=6379

GATEWAY_URL="http://localhost:${GATEWAY_PORT}"
OPENAI_MOCK_URL="http://localhost:${OPENAI_MOCK_PORT}"

PIDS=()
STARTED_REDIS=0

cleanup() {
  echo
  echo "Shutting down..."
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  sleep 1
  # Belt and suspenders: force-kill anything that ignored the polite signal,
  # and also sweep by command line in case a PID was never captured cleanly.
  for pid in "${PIDS[@]:-}"; do
    kill -9 "$pid" >/dev/null 2>&1 || true
  done
  pkill -9 -f "uvicorn llm_sentinel.main:app" >/dev/null 2>&1 || true
  pkill -9 -f "uvicorn mock_providers\.(openai|anthropic)_mock\.main:app" >/dev/null 2>&1 || true
  wait >/dev/null 2>&1 || true
  echo "Stopped."
}
trap cleanup EXIT INT TERM

section() {
  echo
  echo "=================================================================="
  echo "  $1"
  echo "=================================================================="
}

reset_team_alpha_limits() {
  redis-cli -p "$REDIS_PORT" del ratelimit:team-alpha:rpm ratelimit:team-alpha:tpm >/dev/null
}

pretty() {
  "$VENV_PY" -m json.tool 2>/dev/null || cat
}

section "Checking prerequisites"

if [ ! -x "$VENV_UVICORN" ]; then
  echo "No .venv found. Set one up first:"
  echo "  /opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
  exit 1
fi
echo "venv OK"

if ! command -v redis-cli >/dev/null 2>&1; then
  echo "Redis not found. Install with: brew install redis"
  exit 1
fi

if redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
  echo "Redis already running on :$REDIS_PORT"
else
  echo "Starting Redis on :$REDIS_PORT ..."
  redis-server --port "$REDIS_PORT" --daemonize no >/tmp/llm-sentinel-redis.log 2>&1 &
  PIDS+=("$!")
  STARTED_REDIS=1
  sleep 1
fi
redis-cli -p "$REDIS_PORT" flushall >/dev/null

if ! curl -s http://localhost:11434/api/version >/dev/null 2>&1; then
  echo "Ollama isn't reachable at localhost:11434."
  echo "Start it (e.g. 'ollama serve') and pull a model: ollama pull llama3.2:1b"
  exit 1
fi
echo "Ollama OK"

if ! curl -s http://localhost:11434/api/tags | grep -q "llama3.2"; then
  echo "Pulling llama3.2:1b (first run only, may take a minute)..."
  ollama pull llama3.2:1b
fi

section "Starting services"

echo "mock-openai      -> :$OPENAI_MOCK_PORT"
"$VENV_UVICORN" mock_providers.openai_mock.main:app --port "$OPENAI_MOCK_PORT" \
  >/tmp/llm-sentinel-mock-openai.log 2>&1 &
PIDS+=("$!")

echo "mock-anthropic   -> :$ANTHROPIC_MOCK_PORT"
"$VENV_UVICORN" mock_providers.anthropic_mock.main:app --port "$ANTHROPIC_MOCK_PORT" \
  >/tmp/llm-sentinel-mock-anthropic.log 2>&1 &
PIDS+=("$!")

echo "gateway          -> :$GATEWAY_PORT"
"$VENV_UVICORN" llm_sentinel.main:app --port "$GATEWAY_PORT" \
  >/tmp/llm-sentinel-gateway.log 2>&1 &
PIDS+=("$!")

echo -n "Waiting for the gateway to come up"
for _ in $(seq 1 40); do
  if curl -s "$GATEWAY_URL/healthz" >/dev/null 2>&1; then
    echo " ready."
    break
  fi
  echo -n "."
  sleep 0.5
done

section "1. A real completion through the gateway"
echo "> team-alpha asks for gpt-4o-mini (a mock, matching OpenAI's real wire format)"
curl -s -X POST "$GATEWAY_URL/v1/chat/completions" \
  -H "Authorization: Bearer sk-alpha-demo-000111" -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say hi in five words or fewer."}]}' | pretty

section "2. Rate limiting - team-alpha's rpm is intentionally 3"
reset_team_alpha_limits
for i in 1 2 3 4; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$GATEWAY_URL/v1/chat/completions" \
    -H "Authorization: Bearer sk-alpha-demo-000111" -H "Content-Type: application/json" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}')
  echo "  request $i -> $code"
done

section "3. Automatic failover - fault-inject gpt-4o-mini, watch it fall back to real Ollama"
reset_team_alpha_limits
curl -s -X POST "$OPENAI_MOCK_URL/_admin/fault" -H "Content-Type: application/json" \
  -d '{"mode":"error","status_code":500,"rate":1.0}' >/dev/null
echo "fault injected (mock-openai now always returns 500)"
for i in 1 2 3; do
  echo "--- request $i (retries against gpt-4o-mini, then falls back) ---"
  curl -s -X POST "$GATEWAY_URL/v1/chat/completions" \
    -H "Authorization: Bearer sk-alpha-demo-000111" -H "Content-Type: application/json" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}' | pretty
done
curl -s -X POST "$OPENAI_MOCK_URL/_admin/fault" -H "Content-Type: application/json" -d '{"mode":"none"}' >/dev/null
echo "fault cleared"

section "4. Budget enforcement - team-beta's daily budget is smaller than one real call"
curl -s -X POST "$GATEWAY_URL/v1/chat/completions" \
  -H "Authorization: Bearer sk-beta-demo-222333" -H "Content-Type: application/json" \
  -d '{"model":"claude-3-5-sonnet","messages":[{"role":"user","content":"hi"}]}' >/dev/null
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$GATEWAY_URL/v1/chat/completions" \
  -H "Authorization: Bearer sk-beta-demo-222333" -H "Content-Type: application/json" \
  -d '{"model":"claude-3-5-sonnet","messages":[{"role":"user","content":"hi"}]}')
echo "  call 1 -> 200 (succeeds, crosses the limit)   call 2 -> $code (blocked)"

section "5. Live admin change, zero restart"
echo "before:"
curl -s "$GATEWAY_URL/admin/teams/team-alpha" -H "X-Admin-Key: sk-admin-demo-999000" | pretty
curl -s -X PATCH "$GATEWAY_URL/admin/teams/team-alpha" -H "X-Admin-Key: sk-admin-demo-999000" \
  -H "Content-Type: application/json" -d '{"rate_limit": {"rpm": 20}}' >/dev/null
echo "patched rpm to 20, no restart - after:"
curl -s "$GATEWAY_URL/admin/teams/team-alpha" -H "X-Admin-Key: sk-admin-demo-999000" | pretty
# restore the demo value so the next run of this script starts from the same state
curl -s -X PATCH "$GATEWAY_URL/admin/teams/team-alpha" -H "X-Admin-Key: sk-admin-demo-999000" \
  -H "Content-Type: application/json" -d '{"rate_limit": {"rpm": 3}}' >/dev/null

section "6. Prometheus metrics"
curl -s "$GATEWAY_URL/metrics" | grep "^llm_sentinel" | grep -v "^#" || true

section "Everything is running"
cat <<EOF
Gateway:        $GATEWAY_URL
Metrics:        $GATEWAY_URL/metrics
Admin API:      $GATEWAY_URL/admin/teams   (X-Admin-Key: sk-admin-demo-999000)
Mock OpenAI:    $OPENAI_MOCK_URL
Mock Anthropic: http://localhost:$ANTHROPIC_MOCK_PORT

Try it yourself:
  curl -X POST $GATEWAY_URL/v1/chat/completions \\
    -H "Authorization: Bearer sk-alpha-demo-000111" -H "Content-Type: application/json" \\
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'

Tailing the gateway log - press Ctrl+C to stop everything.
EOF

tail -f /tmp/llm-sentinel-gateway.log
