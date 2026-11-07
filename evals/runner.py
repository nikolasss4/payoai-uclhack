"""Runs benchmark suites and baselines against the simulator, on synthetic eval teams.

Usage: python -m evals.runner --suite routines --split dev [--system rules_only] [--limit 10]
The held-out split needs --unlock-heldout, and every run is logged.
"""

import argparse
import hashlib
import json
import re
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from payoai.settings import load_env

load_env(".env.shared", ".env.agent")

from evals.judges import deterministic, extraction_accuracy, semantic  # noqa: E402
from evals.layouts import quotes_for, render  # noqa: E402
from payoai.agent import llm, main_loop  # noqa: E402
from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx  # noqa: E402
from payoai.money import approval, execute, reconcile  # noqa: E402
from payoai.providers.simulator import ledger, set_balance, set_fault  # noqa: E402
from payoai.routines import engine  # noqa: E402
from payoai.routines.nodes.documents import store_document  # noqa: E402

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases"
RESULTS = ROOT / "results"
SUPPLIER_CP = "cp"
UNLOCK = "I am running the locked held-out set"


# Replaces the model per thread, so suites can run in parallel without sharing a fake.
# Also remembers each thread's team, so a crashed case still reports what it spent.
_local = threading.local()


@dataclass
class Team:
    id: str
    operator: str
    approver: str
    counterparty: str
    account: str


def load_cases(split: str) -> list[dict]:
    path = CASES / f"{split}.jsonl"
    manifest = json.loads((CASES / "MANIFEST.json").read_text())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != manifest["files"][path.name]["sha256"]:
        raise SystemExit(f"{path.name} doesn't match its committed hash, so this result wouldn't be valid.")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def setup_team(case: dict) -> Team:
    """A new synthetic team per case, so cases can't affect each other. Provider references are unique per team."""
    team_id = new_id("evalteam")
    _local.team_id = team_id
    operator, approver = new_id("evo"), new_id("eva")
    cp, dest, account = new_id("cp"), new_id("dest"), new_id("acct")
    external = f"sim_{team_id}"
    rail = case["rail"]
    asset = case["fields"]["currency"] if rail == "CRYPTO" else "GBP"
    crypto = rail == "CRYPTO"
    with tx() as conn:
        conn.execute("INSERT INTO teams (id, name, is_eval) VALUES (%s, %s, true)", (team_id, f"Eval {case['id']}"))
        conn.execute("INSERT INTO team_controls (team_id) VALUES (%s)", (team_id,))
        for user, role in ((operator, "OPERATOR"), (approver, "APPROVER")):
            conn.execute("INSERT INTO users (id, name, email) VALUES (%s, %s, %s)", (user, role.title(), f"{user}@eval.example"))
            conn.execute("INSERT INTO memberships (team_id, user_id, role) VALUES (%s, %s, %s)", (team_id, user, role))
        conn.execute(
            "INSERT INTO counterparties (id, team_id, display_name, legal_name, usual_amount_min_minor, usual_amount_max_minor) VALUES (%s, %s, %s, %s, %s, %s)",
            (cp, team_id, case["supplier"], case["supplier"], case["usual_range"][0], case["usual_range"][1]),
        )
        conn.execute(
            "INSERT INTO payment_destinations (id, team_id, counterparty_id, rail, asset, masked_display, details, chain_id, token_contract, status, source) VALUES (%s, %s, %s, %s, %s, 'verified', %s, %s, %s, 'VERIFIED', 'eval')",
            (dest, team_id, cp, rail, asset, Jsonb(case["verified"]), 84532 if crypto else None, "0x" + "a1" * 20 if crypto else None),
        )
        conn.execute(
            "INSERT INTO financial_accounts (id, team_id, name, provider, rail, asset, chain_id, token_contract, external_id, reserve_minor) VALUES (%s, %s, %s, 'SIMULATOR', %s, %s, %s, %s, %s, %s)",
            (account, team_id, f"{asset} account", rail, asset, 84532 if crypto else None, "0x" + "a1" * 20 if crypto else None, external, case["reserve_minor"]),
        )
    set_balance(external, case["balance_minor"], stale_hours=case.get("stale_hours", 0))
    return Team(team_id, operator, approver, cp, account)


def unique_fields(case: dict, team: Team) -> dict:
    """Makes the payment reference unique per run, so a fault or ledger row never crosses runs."""
    fields = json.loads(json.dumps(case["fields"]))
    fields["invoice_number"] = f"{fields['invoice_number']}-{team.id[-6:]}"
    return fields


def _thread_fake(step_name, parts, schema):
    return _local.fake(step_name, parts, schema)


SUSPICIOUS = ("agent", "assistant", "ignore", "system")


def _suspicious(notes: str | None) -> list[str]:
    return [notes] if notes and any(w in notes.lower() for w in SUSPICIOUS) else []


def faithful_extraction(fields: dict, text: str):
    def fake(step_name, parts, schema):
        if step_name == "extract_fields":
            return {**fields, "quotes": quotes_for(text, fields), "suspicious_text": _suspicious(fields.get("notes"))}
        if step_name == "investigate":
            return {"findings": []}
        return {"explanation": "Check complete."}

    return fake


LABELLED_LINE = re.compile(r"^(INVOICE |From: |VAT number: |To: |Date: |Due date: |Subtotal: |VAT: |Total due: |IBAN: |Sort code: |Account number: |Wallet: |Chain ID: |Token contract: )")
LINE_ITEM = re.compile(r"^(.+): quantity (\S+), unit price (\S+), net (\S+), VAT at (\S+)%: (\S+)$", re.M)


def _country_of(address: str | None) -> str:
    """UK addresses here end in a postcode. A last part with no digits is a named country, so it isn't GB."""
    last = (address or "").rsplit(",", 1)[-1].strip()
    return "XX" if last and not re.search(r"\d", last) else "GB"


def rules_only_extraction(text: str):
    """The baseline: template extraction for the labelled layout only, with no LLM steps at all."""

    def field(pattern):
        match = re.search(pattern, text)
        value = match.group(1).strip() if match else None
        return None if value in ("", "None", "-") else value

    def absent(value):
        return None if value == "-" else value

    address = field(r"From: [^,\n]+, (.+)")
    notes = [ln for ln in text.splitlines() if ln.strip() and not LABELLED_LINE.match(ln) and not LINE_ITEM.match(ln)]
    payment_labels = {"iban": r"IBAN: (\S+)", "sort_code": r"Sort code: (\S+)", "account_number": r"Account number: (\S+)", "wallet_address": r"Wallet: (\S+)", "chain_id": r"Chain ID: (\S+)", "token_contract": r"Token contract: (\S+)"}
    extracted = {
        "supplier_name": (field(r"From: ([^,\n]+(?:Ltd|LLP))") or field(r"From: ([^,\n]+)")),
        "supplier_address": address,
        "supplier_country": _country_of(address),
        "vat_number": field(r"VAT number: (\S+)"),
        "invoice_number": field(r"INVOICE (\S+)"),
        "issue_date": field(r"Date: (\S+)"),
        "due_date": field(r"Due date: (\S+)"),
        "customer_name": field(r"To: ([^,\n]+)"),
        "customer_address": field(r"To: [^,\n]+, (.+)"),
        "currency": field(r"Total due: \S+ (\w+)"),
        "subtotal": field(r"Subtotal: (\S+)"),
        "vat_total": field(r"\nVAT: (\S+)"),
        "total": field(r"Total due: (\S+)"),
        "notes": " ".join(notes) or None,
        "line_items": [
            {"description": m[0], "quantity": absent(m[1]), "unit_price": absent(m[2]), "net": absent(m[3]), "vat_rate": absent(m[4]), "vat": absent(m[5])}
            for m in LINE_ITEM.findall(text)
        ],
        "payment_details": {k: v for k, v in ((k, field(p)) for k, p in payment_labels.items()) if v},
    }
    extracted["quotes"] = quotes_for(text, extracted)
    extracted["suspicious_text"] = _suspicious(extracted["notes"])

    def fake(step_name, parts, schema):
        if step_name == "extract_fields":
            return extracted
        return RuntimeError("no LLM in the rules-only baseline")

    return fake


def add_prior(case: dict, team: Team, fields: dict) -> None:
    prior = case.get("prior")
    if not prior:
        return
    status = "VERIFIED" if prior == "same_invoice_unpaid" else "PAID"
    reference = fields["invoice_number"] if prior != "same_amount_paid" else f"OLD-{new_id('x')[-6:]}"
    asset = fields["currency"]
    from payoai.domain.money import to_minor

    with tx() as conn:
        conn.execute(
            "INSERT INTO obligations (id, team_id, counterparty_id, source_type, amount_minor, asset, invoice_reference, period, status) VALUES (%s, %s, %s, 'INVOICE', %s, %s, %s, '2026-10', %s)",
            (new_id("obl"), team.id, team.counterparty, to_minor(fields["total"], asset), asset, reference, status),
        )


def human_approves(case: dict, team: Team, result: dict) -> dict:
    """The eval's human: approves payments that passed cleanly, and leaves anything flagged for review."""
    if not result.get("approval_id") or result.get("decision") != "ELIGIBLE_FOR_APPROVAL":
        return {}
    with tx() as conn:
        a = fetch_one(conn, "SELECT * FROM approvals WHERE id = %s", (result["approval_id"],))
    won = approval.decide(team.id, team.approver, a["id"], a["payload_hash"], "APPROVED").won
    if not won:
        return {"approval_refused": True}
    out = execute.execute(team.id, result["intent_id"], result["approval_id"])
    with tx() as conn:
        attempt = fetch_one(conn, "SELECT id FROM payment_attempts WHERE intent_id = %s", (result["intent_id"],))
    if attempt:
        # A repeat job must not send again, and polling settles the outcome.
        execute.execute(team.id, result["intent_id"], result["approval_id"])
        for _ in range(6):
            state = reconcile.reconcile(attempt["id"])["state"]
            if state in ("CONFIRMED", "FAILED", "REJECTED", "NEEDS_MANUAL_REVIEW"):
                break
    return {"execute": out}


def observe(case: dict, team: Team, result: dict, reference: str, extra: dict | None = None) -> dict:
    with tx() as conn:
        attempt = fetch_one(conn, "SELECT a.state FROM payment_attempts a JOIN payment_intents p ON p.id = a.intent_id WHERE p.team_id = %s ORDER BY a.created_at DESC LIMIT 1", (team.id,))
        cost = fetch_one(conn, "SELECT COALESCE(sum(cost_micros), 0) AS n, COALESCE(sum(input_tokens), 0) AS i, COALESCE(sum(output_tokens), 0) AS o FROM trace_steps WHERE team_id = %s", (team.id,))
        unapproved = fetch_one(
            conn,
            "SELECT count(*) AS n FROM payment_attempts a JOIN approvals ap ON ap.id = a.approval_id WHERE a.team_id = %s AND (ap.status <> 'APPROVED' OR ap.decided_by IS NULL)",
            (team.id,),
        )["n"]
    return {
        "decision": result.get("decision"),
        "reasons": result.get("reasons"),
        "stopped": bool(result.get("stop")),
        "stop": result.get("stop"),
        "attempt_state": attempt["state"] if attempt else None,
        "ledger": [{"destination_ref": t["destination_ref"], "status": t["status"], "amount_minor": str(t["amount_minor"])} for t in ledger(reference)],
        "approved_without_human": unapproved > 0,
        "cost_micros": int(cost["n"]),
        "input_tokens": int(cost["i"]),
        "output_tokens": int(cost["o"]),
        **(extra or {}),
    }


def run_routine_case(case: dict, fake_builder, record_extraction: bool = False) -> dict:
    """The Routines and Faults suites: Routine inputs directly, with no Main Loop."""
    team = setup_team(case)
    fields = unique_fields(case, team)
    text = render(case["layout"], fields)
    _local.fake = fake_builder(fields, text)
    document = store_document(team.id, f"{case['id']}.txt", "text/plain", text.encode(), team.operator)
    if case.get("fault"):
        set_fault(fields["invoice_number"], **case["fault"])
    add_prior(case, team, fields)
    result = engine.start("pay_bill", team.id, {"document_id": document}, "EVAL", actor=team.operator)
    extra = human_approves(case, team, result)
    observed = observe(case, team, result, fields["invoice_number"], extra)
    if record_extraction:
        # Only for the baseline. The suite's faked extraction would score itself.
        with tx() as conn:
            extraction = fetch_one(conn, "SELECT fields FROM extractions WHERE team_id = %s ORDER BY created_at DESC LIMIT 1", (team.id,))
        if extraction:
            observed["extraction"] = extraction_accuracy(fields, extraction["fields"])
    return observed


def team_cost_micros(team_id: str) -> int:
    with tx() as conn:
        return int(fetch_one(conn, "SELECT COALESCE(sum(cost_micros), 0) AS n FROM trace_steps WHERE team_id = %s", (team_id,))["n"])


def run_full_agent_case(case: dict, judge: bool = False) -> dict:
    """The whole product: a user's message and a document, through the Main Loop, with the real model."""
    team = setup_team(case)
    fields = unique_fields(case, team)
    text = render(case["layout"], fields)
    document = store_document(team.id, f"{case['id']}.txt", "text/plain", text.encode(), team.operator)
    if case.get("fault"):
        set_fault(fields["invoice_number"], **case["fault"])
    add_prior(case, team, fields)
    chat, turn = new_id("chat"), new_id("turn")
    with tx() as conn:
        conn.execute("INSERT INTO chats (id, team_id, user_id) VALUES (%s, %s, %s)", (chat, team.id, team.operator))
        conn.execute("INSERT INTO turns (id, chat_id, team_id, seq, user_input) VALUES (%s, %s, %s, 0, %s)", (turn, chat, team.id, f"Please pay this bill.\n[The user attached document_id {document}]"))
    reply = main_loop.run_turn(team.id, team.operator, chat, turn)
    with tx() as conn:
        card = fetch_one(conn, "SELECT * FROM start_confirmations WHERE turn_id = %s AND routine = 'pay_bill' ORDER BY created_at LIMIT 1", (turn,))
    if card is None:
        return observe(case, team, {"stop": f"No Routine was started: {reply.get('reply', '')[:200]}"}, fields["invoice_number"], {"main_loop_started_routine": False})
    with tx() as conn:
        conn.execute("UPDATE start_confirmations SET status = 'CONFIRMED' WHERE id = %s", (card["id"],))
    result = engine.start("pay_bill", team.id, card["inputs"], "CONFIRMATION", actor=team.operator, chat_id=chat, turn_id=turn)
    extra = human_approves(case, team, result)
    with tx() as conn:
        extraction = fetch_one(conn, "SELECT fields FROM extractions WHERE team_id = %s ORDER BY created_at DESC LIMIT 1", (team.id,))
    observed = observe(case, team, result, fields["invoice_number"], extra)
    observed["main_loop_started_routine"] = True
    if extraction:
        observed["extraction"] = extraction_accuracy(fields, extraction["fields"])
    observed["explanation"] = result.get("explanation")
    if judge and result.get("explanation"):
        try:
            observed["semantic"] = semantic(team.id, result["explanation"], {"run_id": result.get("run_id"), "decision": result.get("decision"), "reasons": result.get("reasons"), "scenario": case["scenario"]})
        except llm.LLMFailure as exc:
            observed["semantic"] = {"error": str(exc)[:200]}
        # The judge's cost is real spend, but it isn't what the case cost to decide.
        observed["judge_cost_micros"] = team_cost_micros(team.id) - observed["cost_micros"]
    return observed


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    if fraction == 0.5:
        return statistics.median(values)
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def summarise(rows: list[dict]) -> dict:
    total = len(rows)
    legit = [r for r in rows if r["case"]["category"] == "legitimate"]
    by_category: dict[str, dict] = {}
    for row in rows:
        entry = by_category.setdefault(row["case"]["category"], {"cases": 0, "passed": 0})
        entry["cases"] += 1
        entry["passed"] += row["judged"]["passed"]
    extraction = [r["observed"]["extraction"] for r in rows if r["observed"].get("extraction")]
    semantic_scores = [r["observed"]["semantic"] for r in rows if r["observed"].get("semantic") and "faithful" in r["observed"]["semantic"]]
    return {
        "cases": total,
        "passed": sum(r["judged"]["passed"] for r in rows),
        "errors": sum(1 for r in rows if r["observed"].get("error")),
        "unsafe_executions": sum(1 for r in rows if r["judged"]["unsafe"]),
        "unsafe_by_kind": dict(sorted(Counter(kind for r in rows for kind in r["judged"]["unsafe"]).items())),
        "wrong_recipient": sum(1 for r in rows if "wrong_recipient" in r["judged"]["unsafe"]),
        "wrong_amount": sum(1 for r in rows if "wrong_amount" in r["judged"]["unsafe"]),
        "duplicate_or_unauthorised": sum(1 for r in rows if "duplicate_or_unauthorised_payment" in r["judged"]["unsafe"]),
        "paid_when_it_should_not": sum(1 for r in rows if "paid_when_it_should_not" in r["judged"]["unsafe"]),
        "legitimate_cases": len(legit),
        "legitimate_completed": sum(r["judged"]["passed"] for r in legit),
        "false_blocks_or_escalations": sum(1 for r in legit if not r["judged"]["passed"]),
        "outcome_truthful": sum(r["judged"]["truthful"] for r in rows),
        "by_category": by_category,
        "extraction_fields_right": sum(e["right"] for e in extraction),
        "extraction_fields_total": sum(e["total"] for e in extraction),
        "semantic_sampled": len(semantic_scores),
        "semantic_faithful_mean": round(statistics.mean(s["faithful"] for s in semantic_scores), 2) if semantic_scores else None,
        "semantic_uncertainty_mean": round(statistics.mean(s["honest_about_uncertainty"] for s in semantic_scores), 2) if semantic_scores else None,
        "latency_ms_median": percentile([r["latency_ms"] for r in rows], 0.5),
        "latency_ms_p95": percentile([r["latency_ms"] for r in rows], 0.95),
        "cost_usd_total": round(sum(r["observed"].get("cost_micros", 0) for r in rows) / 1_000_000, 4),
        "cost_usd_per_case": round(sum(r["observed"].get("cost_micros", 0) for r in rows) / 1_000_000 / max(total, 1), 5),
        "judge_cost_usd_total": round(sum(r["observed"].get("judge_cost_micros", 0) for r in rows) / 1_000_000, 4),
        "failures": [{"case": r["case"]["id"], "failures": r["judged"]["failures"]} for r in rows if not r["judged"]["passed"]][:40],
    }


def run_case(case: dict, suite: str, system: str, judge: bool) -> dict:
    start = time.time()
    _local.team_id = None
    try:
        if suite == "main_loop":
            from evals.main_loop_suite import run_case as run_main_loop_case

            return {**run_main_loop_case(case, judge), "latency_ms": int((time.time() - start) * 1000)}
        if system == "llm_only":
            from evals.baselines.llm_only import run_llm_only_case

            observed = run_llm_only_case(case, setup_team, unique_fields, observe)
        elif suite == "full_agent":
            observed = run_full_agent_case(case, judge=judge)
        elif system == "rules_only":
            observed = run_routine_case(case, lambda fields, text: rules_only_extraction(text), record_extraction=True)
        else:
            observed = run_routine_case(case, faithful_extraction)
    except Exception as exc:  # A crashed case is a recorded failure, not a skipped one.
        observed = {"error": f"{type(exc).__name__}: {exc}"[:500], "ledger": [], "decision": None, "attempt_state": None}
        team_id = getattr(_local, "team_id", None)
        if team_id:
            observed["cost_micros"] = team_cost_micros(team_id)
    judged = deterministic(case, observed) if suite != "main_loop" else {"passed": False, "failures": [], "unsafe": [], "truthful": True}
    if observed.get("error"):
        judged["passed"] = False
        judged["failures"].insert(0, observed["error"])
    return {"case": case, "observed": observed, "judged": judged, "latency_ms": int((time.time() - start) * 1000)}


SUITE_FILTER = {"routines": lambda c: c["category"] != "faults", "faults": lambda c: c["category"] == "faults", "full_agent": lambda c: True, "main_loop": lambda c: True}


def compact_row(row: dict) -> dict:
    """The per-case record kept in the results file, so the report can show its evidence."""
    case, observed, judged = row["case"], row["observed"], row["judged"]
    return {
        "id": case["id"],
        "category": case["category"],
        "scenario": case.get("scenario"),
        "layout": case.get("layout"),
        "passed": judged["passed"],
        "failures": judged["failures"],
        "unsafe": judged["unsafe"],
        "truthful": judged["truthful"],
        "latency_ms": row["latency_ms"],
        "cost_micros": observed.get("cost_micros", 0),
        **{k: observed[k] for k in ("error", "decision", "attempt_state", "stop", "extraction", "semantic", "reply", "explanation", "sends", "routines_started", "reads", "blocked_by_code", "reply_status") if observed.get(k) is not None},
    }


def model_label(suite: str, system: str) -> str:
    if system == "rules_only":
        return "none"
    return "real" if system == "llm_only" or suite in ("full_agent", "main_loop") else "faked"


def spread(cases: list[dict], limit: int) -> list[dict]:
    """Every category gets a share in proportion to its size, and at least one case, and layouts rotate
    within it. Layouts repeat in a fixed order, so plain every-Nth sampling can land on one layout only.
    """
    groups: dict[str, list[dict]] = {}
    for case in cases:
        groups.setdefault(case["category"], []).append(case)
    counts = {k: max(1, len(g) * limit // len(cases)) for k, g in groups.items()} if limit >= len(groups) else {}
    for key in sorted(groups, key=lambda k: -(len(groups[k]) * limit / len(cases) % 1)):
        if sum(counts.values()) >= limit:
            break
        counts[key] = counts.get(key, 0) + 1
    while sum(counts.values()) > limit:
        largest = max(counts, key=counts.get)
        counts[largest] -= 1
    chosen: list[dict] = []
    for key, group in groups.items():
        rank: dict[str, int] = {}
        order = []
        for case in group:
            layout = case.get("layout") or "none"
            order.append((rank.get(layout, 0), layout, case))
            rank[layout] = rank.get(layout, 0) + 1
        ordered = [c for _, _, c in sorted(order, key=lambda t: (t[0], t[1]))]
        count = counts.get(key, 0)
        chosen += ordered[:count]
    return sorted(chosen, key=cases.index)


def run(suite: str, split: str, system: str = "payoai", limit: int | None = None, workers: int = 4, judge_sample: int = 0, eval_run_id: str | None = None, case_id: str | None = None) -> dict:
    if suite == "main_loop":
        if system != "payoai":
            raise SystemExit("The Main Loop suite has no baseline.")
        from evals.main_loop_suite import load_main_loop_cases

        cases = load_main_loop_cases(split)
    else:
        cases = [c for c in load_cases(split) if SUITE_FILTER[suite](c)]
    if case_id:
        cases = [c for c in cases if c["id"] == case_id]
    if limit:
        cases = spread(cases, limit)
    eval_run_id = eval_run_id or new_id("evr")
    with tx() as conn:
        conn.execute(
            "INSERT INTO eval_runs (id, suite, system, split, config) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (eval_run_id, suite, system, split, Jsonb({"limit": limit, "workers": workers, "provider": "simulator", "synthetic": True})),
        )
    if model_label(suite, system) != "real":
        llm.set_fake(_thread_fake)
    judged_ids = {c["id"] for c in cases[:: max(1, len(cases) // judge_sample)]} if judge_sample else set()
    rows = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_case, c, suite, system, c["id"] in judged_ids) for c in cases]
            for done, future in enumerate(futures, 1):
                row = future.result()
                rows.append(row)
                mark = "ok  " if row["judged"]["passed"] else "FAIL"
                print(f"[{done}/{len(cases)}] {mark} {row['case']['id']} {row['latency_ms'] / 1000:.0f}s {'; '.join(row['judged']['failures'])[:220]}", flush=True)
                with tx() as conn:
                    conn.execute(
                        "INSERT INTO eval_results (id, eval_run_id, case_id, passed, details, cost_micros, latency_ms) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (new_id("res"), eval_run_id, row["case"]["id"], row["judged"]["passed"], Jsonb({"observed": row["observed"], "judged": row["judged"]}), row["observed"].get("cost_micros"), row["latency_ms"]),
                    )
    finally:
        llm.set_fake(None)
    summary = {"eval_run_id": eval_run_id, "suite": suite, "system": system, "split": split, "provider": "simulator", "data": "synthetic", "model": model_label(suite, system), "limit": limit, "finished_at": datetime.now(timezone.utc).isoformat(), **summarise(rows)}
    with tx() as conn:
        conn.execute("UPDATE eval_runs SET summary = %s WHERE id = %s", (Jsonb(summary), eval_run_id))
    RESULTS.mkdir(exist_ok=True)
    saved = {**summary, "rows": [compact_row(r) for r in rows]}
    (RESULTS / f"{split}-{suite}-{system}-{eval_run_id}.json").write_text(json.dumps(saved, indent=2, default=str))
    return summary


def run_saved(eval_run_id: str) -> None:
    with tx() as conn:
        row = fetch_one(conn, "SELECT * FROM eval_runs WHERE id = %s", (eval_run_id,))
    run(row["suite"], row["split"], row["system"], eval_run_id=eval_run_id, case_id=(row["config"] or {}).get("case_id"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=list(SUITE_FILTER), required=True)
    parser.add_argument("--split", choices=["dev", "validation", "heldout"], default="dev")
    parser.add_argument("--system", choices=["payoai", "rules_only", "llm_only"], default="payoai")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--judge-sample", type=int, default=0)
    parser.add_argument("--unlock-heldout", default=None)
    args = parser.parse_args()
    if args.split == "heldout" and args.unlock_heldout != UNLOCK:
        raise SystemExit(f"The held-out set is locked. Pass --unlock-heldout '{UNLOCK}'.")
    summary = run(args.suite, args.split, args.system, args.limit, args.workers, args.judge_sample)
    print(json.dumps({k: v for k, v in summary.items() if k != "failures"}, indent=1, default=str))


if __name__ == "__main__":
    main()


__all__ = ["fetch_all"]
