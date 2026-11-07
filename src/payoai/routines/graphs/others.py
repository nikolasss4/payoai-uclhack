"""The remaining Routines, built from the same shared Nodes."""

from langgraph.graph import END, START, StateGraph

from payoai import memory
from payoai.routines import accounting, contacts, purchases, recurring
from payoai.routines.graphs.payment import _add_checks, _add_finish, _after
from payoai.routines.nodes import checks, decision, documents, extra
from payoai.routines.nodes.common import RunState, ask_user, node


def _single(fn) -> StateGraph:
    g = StateGraph(RunState)
    g.add_node("only", fn)
    g.add_edge(START, "only")
    g.add_edge("only", END)
    return g


def pay_someone() -> StateGraph:
    g = StateGraph(RunState)
    g.add_node("require_purpose", extra.require_purpose)
    g.add_node("ask_purpose", ask_user)
    g.add_node("match_counterparty", checks.match_counterparty)
    g.add_node("ask_counterparty", ask_user)
    g.add_node("register_obligation", checks.register_obligation)
    g.add_edge(START, "require_purpose")
    g.add_conditional_edges("require_purpose", _after("match_counterparty", "ask_purpose"))
    g.add_edge("ask_purpose", "require_purpose")
    g.add_conditional_edges("match_counterparty", _after("register_obligation", "ask_counterparty"))
    g.add_edge("ask_counterparty", "match_counterparty")
    _add_checks(g, "register_obligation", include_compliance=False)
    _add_finish(g, prepare=True)
    return g


def replace_bill() -> StateGraph:
    g = StateGraph(RunState)
    g.add_node("prepare_replacement", extra.prepare_replacement)
    g.add_node("load_document", documents.load_document_for_replacement)
    g.add_node("extract_fields", documents.extract_fields)
    g.add_node("apply_corrections", extra.apply_corrections)
    g.add_node("match_counterparty", checks.match_counterparty)
    g.add_node("ask_counterparty", ask_user)
    g.add_node("register_obligation", checks.register_obligation)
    g.add_node("record_corrections", extra.record_corrections)
    g.add_edge(START, "prepare_replacement")
    g.add_conditional_edges("prepare_replacement", _after("load_document"))
    g.add_conditional_edges("load_document", _after("extract_fields"))
    g.add_conditional_edges("extract_fields", _after("apply_corrections"))
    g.add_edge("apply_corrections", "match_counterparty")
    g.add_conditional_edges("match_counterparty", _after("register_obligation", "ask_counterparty"))
    g.add_edge("ask_counterparty", "match_counterparty")
    g.add_edge("register_obligation", "record_corrections")
    _add_checks(g, "record_corrections", include_compliance=True)
    _add_finish(g, prepare=True)
    return g


def check_bill_compliance() -> StateGraph:
    g = StateGraph(RunState)
    g.add_node("load_document", documents.load_document)
    g.add_node("extract_fields", documents.extract_fields)
    g.add_node("report", extra.compliance_report)
    g.add_node("explain", decision.explain_without_decision_record)
    g.add_edge(START, "load_document")
    g.add_conditional_edges("load_document", _after("extract_fields"))
    g.add_conditional_edges("extract_fields", _after("report"))
    g.add_edge("report", "explain")
    g.add_edge("explain", END)
    return g


def prepare_batch() -> StateGraph:
    return _single(extra.prepare_batch)


def cash_analysis() -> StateGraph:
    g = StateGraph(RunState)
    g.add_node("project", extra.cash_analysis)
    g.add_node("explain", extra.explain_cash)
    g.add_edge(START, "project")
    g.add_edge("project", "explain")
    g.add_edge("explain", END)
    return g


def add_counterparty() -> StateGraph:
    return _single(contacts.add_counterparty)


def set_up_recurring() -> StateGraph:
    return _single(recurring.draft_mandate)


def prepare_occurrence() -> StateGraph:
    g = StateGraph(RunState)
    g.add_node("claim_occurrence", recurring.claim_occurrence)
    g.add_node("load_mandate", recurring.load_mandate)
    g.add_node("register_obligation", checks.register_obligation)
    g.add_node("check_mandate", recurring.check_mandate)
    g.add_edge(START, "claim_occurrence")
    g.add_conditional_edges("claim_occurrence", _after("load_mandate"))
    g.add_conditional_edges("load_mandate", _after("register_obligation"))
    g.add_edge("register_obligation", "check_mandate")
    _add_checks(g, "check_mandate", include_compliance=False)
    _add_finish(g, prepare=True)
    return g


def revise_expense() -> StateGraph:
    return _single(accounting.revise_expense)


def chase_missing_document() -> StateGraph:
    return _single(accounting.open_exception)


def purchase_research() -> StateGraph:
    g = StateGraph(RunState)
    g.add_node("capture", purchases.capture_constraints)
    g.add_node("ask_budget", ask_user)
    g.add_node("research", purchases.research)
    g.add_node("verify", purchases.verify_research_quotes)
    g.add_node("rank", purchases.filter_and_rank)
    g.add_edge(START, "capture")
    g.add_conditional_edges("capture", _after("research", "ask_budget"))
    g.add_edge("ask_budget", "capture")
    g.add_conditional_edges("research", _after("verify"))
    g.add_edge("verify", "rank")
    g.add_edge("rank", END)
    return g


def purchase_checkout() -> StateGraph:
    return _single(purchases.prepare_checkout)


@node("Save Preference", step_type="MEMORY_WRITE")
def save_preference_node(state: RunState, ctx) -> dict:
    inputs = state["inputs"]
    try:
        memory_id = memory.save(state["team_id"], None if inputs.get("scope", "team") == "team" else state.get("actor"), inputs["type"], inputs.get("subject"), inputs["value"], "confirmed", [state["run_id"]])
    except memory.MemoryRefused as exc:
        return {"stop": str(exc)}
    return {"result": {"memory_id": memory_id}}


def save_preference() -> StateGraph:
    return _single(save_preference_node)
