"""Pay Bill, Register Bill and Pay Someone: the same fixed Nodes in slightly different orders."""

from langgraph.graph import END, START, StateGraph

from payoai.routines.nodes import checks, decision, documents
from payoai.routines.nodes.common import RunState, ask_user


def _after(next_node: str, ask: str | None = None):
    """Early-stop and question routing, shared by every Node that can do either."""

    def route(state: RunState) -> str:
        if state.get("stop"):
            return END
        if ask and state.get("pending_question"):
            return ask
        return next_node

    return route


def _add_checks(g: StateGraph, first_after: str, include_compliance: bool) -> None:
    """Check Duplicates through Decide. All checks run even if one fails."""
    g.add_node("check_duplicates", checks.check_duplicates)
    g.add_node("check_amount", checks.check_amount)
    g.add_node("select_destination", checks.select_destination)
    g.add_node("ask_destination", ask_user)
    g.add_node("check_destination", checks.check_destination)
    g.add_node("select_source_account", checks.select_source_account)
    g.add_node("ask_source", ask_user)
    g.add_node("check_cash", checks.check_cash)
    g.add_node("decide", decision.decide)
    g.add_edge(first_after, "check_duplicates")
    if include_compliance:
        g.add_node("check_compliance", checks.check_compliance)
        g.add_edge("check_duplicates", "check_compliance")
        g.add_edge("check_compliance", "check_amount")
    else:
        g.add_edge("check_duplicates", "check_amount")
    g.add_edge("check_amount", "select_destination")
    g.add_conditional_edges("select_destination", _after("check_destination", "ask_destination"))
    g.add_edge("ask_destination", "select_destination")
    g.add_edge("check_destination", "select_source_account")
    g.add_conditional_edges("select_source_account", _after("check_cash", "ask_source"))
    g.add_edge("ask_source", "select_source_account")
    g.add_edge("check_cash", "decide")


def _add_finish(g: StateGraph, prepare: bool) -> None:
    """Investigate only on NEEDS_REVIEW or NEEDS_INFORMATION, then Explain, then prepare if allowed."""
    g.add_node("investigate", decision.investigate)
    g.add_node("explain", decision.explain)

    def after_decide(state: RunState) -> str:
        return "investigate" if state["decision"] in ("NEEDS_REVIEW", "NEEDS_INFORMATION") else "explain"

    g.add_conditional_edges("decide", after_decide)
    g.add_edge("investigate", "explain")
    if not prepare:
        g.add_edge("explain", END)
        return
    g.add_node("prepare_payment", decision.prepare_payment)

    def after_explain(state: RunState) -> str:
        can_prepare = state["decision"] in ("ELIGIBLE_FOR_APPROVAL", "NEEDS_REVIEW") and state.get("destination_id") and state.get("source_account_id") and state.get("amount_minor")
        return "prepare_payment" if can_prepare else END

    g.add_conditional_edges("explain", after_explain)
    g.add_edge("prepare_payment", END)


def _bill_graph(prepare: bool) -> StateGraph:
    g = StateGraph(RunState)
    g.add_node("load_document", documents.load_document)
    g.add_node("extract_fields", documents.extract_fields)
    g.add_node("match_counterparty", checks.match_counterparty)
    g.add_node("ask_counterparty", ask_user)
    g.add_node("register_obligation", checks.register_obligation)
    g.add_node("load_registered", checks.load_registered)
    # A registered bill reuses its file and extraction, then re-runs every check.
    g.add_conditional_edges(START, lambda s: "load_registered" if s["inputs"].get("obligation_id") else "load_document")
    g.add_conditional_edges("load_document", _after("extract_fields"))
    g.add_conditional_edges("extract_fields", _after("match_counterparty"))
    g.add_conditional_edges("match_counterparty", _after("register_obligation", "ask_counterparty"))
    g.add_edge("ask_counterparty", "match_counterparty")
    _add_checks(g, "register_obligation", include_compliance=True)
    g.add_conditional_edges("load_registered", _after("check_duplicates"))
    _add_finish(g, prepare)
    return g


def pay_bill() -> StateGraph:
    return _bill_graph(prepare=True)


def register_bill() -> StateGraph:
    return _bill_graph(prepare=False)
