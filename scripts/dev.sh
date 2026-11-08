#!/usr/bin/env bash
# Starts the API, three workers and the test shop. Ctrl-C stops them all.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD:$PWD/src"
export PYTHONWARNINGS=ignore
mkdir -p data/logs

# Ports. Override with API_PORT=... MERCHANT_PORT=... scripts/dev.sh if either is taken.
API_PORT="${API_PORT:-8743}"
MERCHANT_PORT="${MERCHANT_PORT:-8793}"
export MERCHANT_URL="${MERCHANT_URL:-http://127.0.0.1:$MERCHANT_PORT}"

# The app is a React build in web/dist. Build it once, or again after a change in frontend/.
if [ ! -f web/dist/index.html ] || [ -n "$(find frontend/src frontend/index.html -newer web/dist/index.html 2>/dev/null | head -1)" ]; then
  (cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build) > data/logs/frontend.log 2>&1 || { echo "Frontend build failed. See data/logs/frontend.log"; exit 1; }
fi

trap 'kill 0' EXIT
uv run -q python -m uvicorn merchant_site.app:app --port "$MERCHANT_PORT" --log-level warning > data/logs/merchant.log 2>&1 &
uv run -q python -m payoai.workers.agent_worker > data/logs/agent.log 2>&1 &
uv run -q python -m payoai.workers.money_worker > data/logs/money.log 2>&1 &
uv run -q python -m payoai.workers.scheduler > data/logs/scheduler.log 2>&1 &
uv run -q python -m uvicorn payoai.api.app:app --port "$API_PORT" --log-level warning > data/logs/api.log 2>&1 &
echo "PayoAI is at http://127.0.0.1:$API_PORT (synthetic data only). Logs are in data/logs."
wait
