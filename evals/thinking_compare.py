"""Compares extract_fields at two thinking levels on the same dev invoices, with the real model.

Usage: python -m evals.thinking_compare [--cases 10] [--levels low medium] [--workers 3]

The override only exists in this process: each level is added to the step table under its own name, so
src is untouched and the product still uses the level set in agent/config.py. Dev split only.
"""

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone

from evals import runner
from evals.judges import extraction_accuracy
from evals.layouts import render
from payoai.agent import llm
from payoai.agent.config import STEPS
from payoai.db import fetch_one, new_id, tx
from payoai.routines.nodes.documents import EXTRACTION_PROMPT, EXTRACTION_SCHEMA, verify_quotes
from payoai.trace import TraceContext


def _extract(case: dict, level: str) -> dict:
    team = runner.setup_team(case)
    fields = runner.unique_fields(case, team)
    text = render(case["layout"], fields)
    run_id = new_id("run")
    with tx() as conn:
        conn.execute("INSERT INTO routine_runs (id, team_id, routine, trigger_type, inputs, status) VALUES (%s, %s, 'thinking_compare', 'EVAL', '{}', 'COMPLETED')", (run_id, team.id))
    parts = [llm.text_part(EXTRACTION_PROMPT), llm.untrusted("invoice", text)]
    start = time.time()
    row = {"case": case["id"], "layout": case["layout"], "scenario": case["scenario"], "level": level}
    try:
        data = llm.call(f"extract_fields_{level}", parts, TraceContext(team.id, run_id=run_id), schema=EXTRACTION_SCHEMA, trust_label="UNTRUSTED").data
        row["accuracy"] = extraction_accuracy(fields, data)
        row["failed_quotes"] = len(verify_quotes(data, text))
        row["flagged_suspicious"] = bool(data.get("suspicious_text"))
    except llm.LLMFailure as exc:
        row["error"] = str(exc)[:300]
    row["latency_ms"] = int((time.time() - start) * 1000)
    row["cost_micros"] = runner.team_cost_micros(team.id)
    with tx() as conn:
        row["output_tokens"] = int(fetch_one(conn, "SELECT COALESCE(sum(output_tokens), 0) AS n FROM trace_steps WHERE team_id = %s", (team.id,))["n"])
    return row


def _summary(rows: list[dict]) -> dict:
    latencies = [r["latency_ms"] for r in rows]
    done = [r for r in rows if "accuracy" in r]
    return {
        "calls": len(rows),
        "errors": len(rows) - len(done),
        "fields_right": sum(r["accuracy"]["right"] for r in done),
        "fields_total": sum(r["accuracy"]["total"] for r in done),
        "invoices_all_right": sum(1 for r in done if not r["accuracy"]["wrong"]),
        "failed_quotes": sum(r["failed_quotes"] for r in done),
        "latency_ms_median": statistics.median(latencies),
        "latency_ms_p95": runner.percentile(latencies, 0.95),
        "cost_usd_total": round(sum(r["cost_micros"] for r in rows) / 1_000_000, 4),
        "cost_usd_per_call": round(sum(r["cost_micros"] for r in rows) / 1_000_000 / max(len(rows), 1), 5),
        "wrong": [{"case": r["case"], "wrong": r["accuracy"]["wrong"]} for r in done if r["accuracy"]["wrong"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=10)
    parser.add_argument("--levels", nargs=2, default=["low", "medium"])
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    for level in args.levels:
        STEPS[f"extract_fields_{level}"] = replace(STEPS["extract_fields"], thinking_level=level)
    cases = [c for c in runner.load_cases("dev") if c["category"] != "faults"]
    cases = cases[:: max(1, len(cases) // args.cases)][: args.cases]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(lambda pair: _extract(*pair), [(c, level) for c in cases for level in args.levels]))
    result = {
        "suite": "thinking_compare",
        "step": "extract_fields",
        "split": "dev",
        "data": "synthetic",
        "model": STEPS["extract_fields"].model,
        "cases": [c["id"] for c in cases],
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "levels": {level: _summary([r for r in rows if r["level"] == level]) for level in args.levels},
        "rows": rows,
    }
    runner.RESULTS.mkdir(exist_ok=True)
    (runner.RESULTS / f"dev-thinking_compare-{new_id('evr')}.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result["levels"], indent=1, default=str))


if __name__ == "__main__":
    main()
