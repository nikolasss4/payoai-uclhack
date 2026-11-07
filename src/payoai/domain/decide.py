"""Combines check results into one decision. The worst result wins, and no model takes part."""

POLICY_VERSION = "payment-risk-1.0"
ORDER = ["ELIGIBLE_FOR_APPROVAL", "NEEDS_INFORMATION", "NEEDS_REVIEW", "BLOCK"]


def combine(checks: list[dict]) -> tuple[str, list[dict]]:
    worst = "ELIGIBLE_FOR_APPROVAL"
    reasons = []
    for check in checks:
        outcome = check["outcome"]
        if outcome in ("PASS", "NOT_APPLICABLE", "ELIGIBLE_FOR_APPROVAL"):
            continue
        reasons.append({"rule_id": check["rule_id"], "outcome": outcome, "reason": check["reason"], "check_id": check.get("id")})
        if ORDER.index(outcome) > ORDER.index(worst):
            worst = outcome
    return worst, reasons


def same_details(a: str | None, b: str | None) -> bool:
    """Full equality after removing spacing and case. A similar-looking value is never a match."""
    if a is None or b is None:
        return False
    return "".join(str(a).split()).lower() == "".join(str(b).split()).lower()
