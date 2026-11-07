#!/usr/bin/env bash
# Starts the API, three workers and the test shop. Ctrl-C stops them all.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD:$PWD/src"
export PYTHONWARNINGS=ignore
mkdir -p data/logs

trap 'kill 0' EXIT
uv run -q python -m uvicorn merchant_site.app:app --port 8090 --log-level warning > data/logs/merchant.log 2>&1 &
uv run -q python -m payoai.workers.agent_worker > data/logs/agent.log 2>&1 &
uv run -q python -m payoai.workers.money_worker > data/logs/money.log 2>&1 &
uv run -q python -m payoai.workers.scheduler > data/logs/scheduler.log 2>&1 &
uv run -q python -m uvicorn payoai.api.app:app --port 8000 --log-level warning > data/logs/api.log 2>&1 &
echo "PayoAI is at http://127.0.0.1:8000 (synthetic data only). Logs are in data/logs."
wait
