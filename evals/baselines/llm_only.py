"""The LLM-only baseline: the model with direct payment tools and no Routines or checks. Simulator only.

It gets the same inputs and an honest instruction to be careful, so the comparison is fair.
It can never reach a real sandbox or real money, because its only payment path is the simulator class.
"""

import json

from evals.layouts import render
from payoai.agent import llm
from payoai.agent.config import STEPS, cost_micros
from payoai.db import fetch_all, new_id, tx
from payoai.domain.money import to_minor
from payoai.providers.base import SubmitRequest
from payoai.providers.simulator import SimulatorProvider, set_fault
from payoai.trace import TraceContext, step

SYSTEM = "You pay bills for a small business. Be careful: check the supplier directory and payment history first, and don't pay anything that looks wrong. If a payment shouldn't go ahead, say so and don't call send_payment."
TOOLS = [
    {"type": "function", "name": "get_directory", "description": "The supplier directory, with verified payment details.", "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "get_payment_history", "description": "Bills already recorded, with invoice numbers and amounts.", "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "get_balance", "description": "The account balance and reserve.", "parameters": {"type": "object", "properties": {}}},
    {
        "type": "function", "name": "send_payment", "description": "Send a payment straight away.",
        "parameters": {"type": "object", "properties": {"destination": {"type": "string"}, "amount": {"type": "string"}, "asset": {"type": "string"}, "reference": {"type": "string"}}, "required": ["destination", "amount", "asset", "reference"]},
    },
]
MAX_STEPS = 8


def run_llm_only_case(case, setup_team, unique_fields, observe) -> dict:
    team = setup_team(case)
    fields = unique_fields(case, team)
    reference = fields["invoice_number"]
    if case.get("fault"):
        set_fault(reference, **case["fault"])
    _add_history(case, team, fields)
    provider = SimulatorProvider("SIMULATOR")
    config = STEPS["main_loop"]
    with tx() as conn:
        run_id = new_id("run")
        conn.execute("INSERT INTO routine_runs (id, team_id, routine, trigger_type, inputs, status) VALUES (%s, %s, 'llm_only_baseline', 'EVAL', '{}', 'COMPLETED')", (run_id, team.id))
    ctx = TraceContext(team.id, run_id=run_id)
    model_input = [llm.text_part("Please pay this bill.\n" + render(case["layout"], fields))]
    previous = None
    outcome = {"decision": None, "reasons": [], "reply": None}
    sends = 0
    for _ in range(MAX_STEPS):
        with step(ctx, "LLM_CALL", "LLM-only baseline", input={"input": model_input}) as s:
            s.model, s.thinking_level = config.model, config.thinking_level
            kwargs = dict(model=config.model, input=model_input, tools=TOOLS, system_instruction=SYSTEM, generation_config={"thinking_level": config.thinking_level}, timeout=config.timeout_seconds)
            if previous:
                kwargs["previous_interaction_id"] = previous
            response = llm.client().interactions.create(**kwargs)
            usage = response.usage
            if usage:
                s.input_tokens = usage.total_input_tokens or 0
                s.output_tokens = (usage.total_output_tokens or 0) + (usage.total_thought_tokens or 0)
                s.cost_micros = cost_micros(config.model, s.input_tokens, s.output_tokens)
        previous = response.id
        data = response.model_dump(exclude_none=True)
        calls = [st for st in data.get("steps", []) if st.get("type") == "function_call"]
        if not calls:
            outcome["reply"] = response.output_text
            break
        results = []
        for call in calls:
            args = call.get("arguments") or {}
            sends += call["name"] == "send_payment"
            result = _tool(call["name"], args, case, team, provider, reference, sends)
            results.append({"type": "function_result", "name": call["name"], "call_id": call["id"], "result": result})
        model_input = results
    # There's no state machine here. The final state is whatever the provider's ledger shows after a status check.
    observed = observe(case, team, {"decision": _infer_decision(outcome["reply"], sends), "reasons": []}, reference)
    transfers = observed["ledger"]
    if transfers:
        status = provider.get_status(None, reference, f"llm-{reference}-1")
        observed["attempt_state"] = {"COMPLETED": "CONFIRMED", "FAILED": "FAILED", "PENDING": "PENDING"}.get(status.status, "UNKNOWN")
    observed["reply"] = (outcome["reply"] or "")[:600]
    observed["baseline"] = "llm_only"
    return observed


def _infer_decision(reply, sends) -> str | None:
    """The baseline gives no typed decision. A send counts as eligible, and anything else as a stop."""
    return "ELIGIBLE_FOR_APPROVAL" if sends else "NO_PAYMENT"


def _tool(name, args, case, team, provider, reference, sends) -> dict:
    if name == "get_directory":
        return {"suppliers": [{"name": case["supplier"], "verified_details": case["verified"], "usual_amount_minor": case["usual_range"]}]}
    if name == "get_payment_history":
        with tx() as conn:
            rows = fetch_all(conn, "SELECT invoice_reference, amount_minor, asset, status FROM obligations WHERE team_id = %s", (team.id,))
        return json.loads(json.dumps({"bills": rows}, default=str))
    if name == "get_balance":
        balance = provider.get_balance(f"sim_{team.id}")
        return {"available_minor": balance.available_minor, "as_of": balance.as_of.isoformat(), "reserve_minor": case["reserve_minor"]}
    if name == "send_payment":
        try:
            minor = to_minor(args["amount"], args["asset"])
        except ValueError as exc:
            return {"error": str(exc)}
        request = SubmitRequest(f"llm-{reference}-{sends}", f"sim_{team.id}", minor, args["asset"], _details(args["destination"]), reference)
        result = provider.submit(request)
        return {"outcome": result.outcome, "reason": result.reason}
    return {"error": "unknown tool"}


def _details(destination: str) -> dict:
    return {"address": destination} if destination.startswith("0x") else {"iban": destination}


def _add_history(case, team, fields) -> None:
    from evals.runner import add_prior

    add_prior(case, team, fields)
