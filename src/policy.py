"""Deterministic policy engine (data/procurement_policy.md as code).

Everything here is plain code: thresholds, date math, required fields, review
triggers. The LLM never computes these; it can only add to them.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from src import data_access

# Section 4 - (upper bound inclusive, approvals). None = no upper bound.
APPROVAL_TIERS = [
    (1_000, ["Manager"]),
    (10_000, ["Department Head", "Procurement"]),
    (25_000, ["Department Head", "Finance", "Procurement"]),
    (None, ["Department Head", "Finance", "CFO", "Procurement"]),
]
CANONICAL_APPROVALS = ["Manager", "Department Head", "Procurement", "Finance", "CFO", "Security", "Privacy", "Legal"]
SECURITY_REVIEW_VALID_DAYS = 365  # Section 5
LEGAL_NEW_VENDOR_THRESHOLD = 10_000  # Section 7 (">= $10,000")

# Section 5 triggers, matched against data_access_level / integrations (lowercased substrings).
SENSITIVE_DATA_MARKERS = ["source code", "confidential", "pii", "credential", "secret", "production"]
SENSITIVE_INTEGRATION_MARKERS = ["production", "cloud account", "git", "source code", "code repositor"]
PII_MARKERS = ["pii", "personal"]
UNKNOWN_VALUES = {None, "", "unknown", "n/a", "na", "tbd", "none specified"}

# Section 9 - phrases that try to change the copilot's rules. Heuristic floor; the LLM may flag more.
# ponytail: regex heuristic, swap for a classifier if adversarial text gets subtler than this.
INJECTION_PATTERNS = [
    r"ignore (all |any |the |previous |prior )*(procurement |security )?(rules|instructions|policy|policies|controls)",
    r"disregard (all |any |the |previous )*(rules|instructions|policy)",
    r"(treat|mark|consider) (this|it|the request) as [\w\- ]*approved",
    r"\b(cfo|ceo|vp|security|legal|finance)[\- ]approved\b",
    r"\bpre-?approved\b",
    r"approve (it|this|the request)? ?(immediately|now|automatically)",
    r"(skip|bypass|override) (the )?(security|privacy|legal|procurement|approval|review)",
    r"\b(system|assistant) ?(prompt|note|message|override)\s*:",
    r"you are now\b",
    r"no approvals? (are )?(needed|required)",
]


def reference_date() -> date:
    """Snapshot date declared in the policy file - never the machine clock."""
    m = re.search(r"reference date:\*{0,2}\s*(\d{4}-\d{2}-\d{2})", data_access.load_policy_text())
    if not m:
        raise RuntimeError("procurement_policy.md does not declare a reference date")
    return date.fromisoformat(m.group(1))


def approvals_for_amount(amount: float | None) -> list[str]:
    if amount is None:
        return []
    for upper, approvals in APPROVAL_TIERS:
        if upper is None or amount <= upper:
            return list(approvals)
    return []


def review_is_current(review_date: str | None, ref: date) -> bool:
    if not review_date:
        return False
    return date.fromisoformat(str(review_date)) + timedelta(days=SECURITY_REVIEW_VALID_DAYS) >= ref


def find_injection(texts: list[str]) -> list[str]:
    hits = []
    for text in texts:
        for pattern in INJECTION_PATTERNS:
            m = re.search(pattern, text or "", flags=re.IGNORECASE)
            if m:
                hits.append(m.group(0))
    return hits


def missing_fields(req: dict, requester: dict | None) -> list[str]:
    """Section 1 - required request information."""
    missing = []
    if requester is None:
        missing.append("requester and department (requester_id not found in employee records)")
    if not req.get("product_name") or not req.get("vendor_name"):
        missing.append("product/vendor")
    if req.get("annual_cost_usd") is None:
        missing.append("annual cost (or a reasonable annual estimate)")
    if not req.get("user_count"):
        missing.append("number of users/licenses")
    if not str(req.get("business_justification") or "").strip():
        missing.append("business purpose")
    if str(req.get("data_access_level") or "").strip().lower() in UNKNOWN_VALUES:
        missing.append("intended data-access level")
    if req.get("requested_integrations") is None:
        missing.append("required integrations")
    return missing


def _has(text: str, markers: list[str]) -> bool:
    # Word-start match so "git" hits "Git repositories" but not "digital".
    text = text.replace("_", " ")  # "customer_pii" -> "customer pii"
    return any(re.search(rf"\b{re.escape(m)}", text, flags=re.IGNORECASE) for m in markers)


def evaluate(req: dict, requester: dict | None, budget: dict, vendor: dict, catalog_matches: list[dict]) -> dict:
    """Apply every deterministic rule. Inputs are tool outputs; returns the decision floor."""
    approvals: list[str] = []
    flags: list[str] = []
    reasons: list[dict] = []  # {"rule", "finding", "reference"}

    def add(items: list[str], *new: str) -> None:
        items.extend(n for n in new if n not in items)

    def why(rule: str, finding: str) -> None:
        reasons.append({"rule": rule, "finding": finding})

    missing = missing_fields(req, requester)
    if missing:
        add(flags, "missing_information")
        why("Policy section 1", "Request is not ready for approval; missing: " + "; ".join(missing))

    cost = req.get("annual_cost_usd")
    tier = approvals_for_amount(cost)
    if tier:
        add(approvals, *tier)
        why("Policy section 4", f"Annual amount ${cost:,.2f} requires: {', '.join(tier)}")
    else:
        add(approvals, "Procurement")
        why("Policy section 4", "Annual amount unknown - threshold cannot be computed; Procurement must triage")

    # Section 2 - budget
    if budget["status"] == "insufficient":
        add(flags, "budget_insufficient")
        add(approvals, "Finance")
        why("Policy section 2", budget["finding"])
    elif budget["status"] == "unverified":
        add(flags, "budget_unverified")
        add(approvals, "Finance")
        why("Policy section 2", budget["finding"])

    # Section 3 - overlap (same category in the approved catalog)
    same_category = [c for c in catalog_matches if c.get("match") == "same_category"]
    if same_category:
        add(flags, "existing_tool_overlap")
        names = ", ".join(f"{c['product_name']} ({c['software_id']})" for c in same_category)
        why("Policy section 3", f"Approved catalog already has {req.get('category')} tools: {names}")

    # Section 5 - security
    data_level = str(req.get("data_access_level") or "")
    integrations = " ".join(req.get("requested_integrations") or [])
    data_unknown = data_level.strip().lower() in UNKNOWN_VALUES
    security_reasons = []
    if _has(data_level, SENSITIVE_DATA_MARKERS):
        security_reasons.append(f"data access level '{data_level}'")
    if _has(integrations, SENSITIVE_INTEGRATION_MARKERS):
        security_reasons.append(f"integration '{integrations}'")
    if data_unknown:
        security_reasons.append("data access level not stated (cannot rule out sensitive data)")
    if not vendor["assessment_current"]:
        security_reasons.append(f"vendor security assessment: {vendor['assessment_summary']}")
    if security_reasons:
        add(flags, "security_review_required")
        add(approvals, "Security")
        why("Policy section 5", "Security review required: " + "; ".join(security_reasons))
    if vendor["review_expired"]:
        add(flags, "vendor_review_expired")
    if vendor["conflicts"]:
        add(flags, "conflicting_vendor_evidence")
        add(approvals, "Security")
        why("Policy section 5", "Registry and vendor-risk service disagree: " + "; ".join(vendor["conflicts"]))
    if not vendor["risk_api_available"]:
        add(flags, "vendor_risk_unavailable")
        add(approvals, "Security")
        why("Policy section 10", f"Vendor-risk service could not be verified: {vendor['risk_api_error']}")

    # Section 6 - privacy
    pii = _has(data_level, PII_MARKERS)
    outside_region = vendor.get("stores_data_outside_region") is True
    sensitive = pii or _has(data_level, SENSITIVE_DATA_MARKERS) or data_unknown
    residency_unverified = not vendor["risk_api_available"]  # Section 10: never infer the favorable answer
    if pii or (sensitive and (outside_region or residency_unverified)):
        add(flags, "privacy_review_required")
        add(approvals, "Privacy")
        detail = []
        if pii:
            detail.append(f"processes {data_level}")
        if outside_region and sensitive:
            detail.append("vendor stores data outside the operating region")
        if residency_unverified and sensitive:
            detail.append("data residency cannot be verified (vendor-risk service unavailable)")
        why("Policy section 6", "Privacy review required: " + "; ".join(detail))

    # Section 7 - legal
    legal_reasons = []
    if vendor["is_new_vendor"] and cost is not None and cost >= LEGAL_NEW_VENDOR_THRESHOLD:
        legal_reasons.append(f"new vendor with annual spend ${cost:,.0f} >= ${LEGAL_NEW_VENDOR_THRESHOLD:,}")
    if vendor["legal_terms_status"] != "Approved":
        legal_reasons.append(f"legal terms status is '{vendor['legal_terms_status']}'")
    if outside_region and sensitive:
        legal_reasons.append("cross-region data-processing issue")
    if legal_reasons:
        add(flags, "legal_review_required")
        add(approvals, "Legal")
        why("Policy section 7", "Legal review required: " + "; ".join(legal_reasons))

    # Section 9 - untrusted content
    texts = [str(req.get(k) or "") for k in ("product_name", "vendor_name", "category", "business_justification", "data_access_level")]
    texts += [str(x) for x in (req.get("requested_integrations") or [])]
    texts += [str(vendor.get("registry_notes") or ""), str(vendor.get("risk_api_notes") or "")]
    injections = find_injection(texts)
    if injections:
        add(flags, "prompt_injection_detected")
        why("Policy section 9", "Embedded instructions ignored (treated as data): " + "; ".join(f'"{h}"' for h in injections))

    return {
        "required_approvals": [a for a in CANONICAL_APPROVALS if a in approvals],
        "risk_flags": flags,
        "missing_information": missing,
        "reasons": reasons,
        "recommendation_code": floor_recommendation(flags),
    }


# Severity order: a higher code can never be downgraded by the LLM.
RECOMMENDATIONS = {
    "route_for_review": (0, "Route for required approvals"),
    "reuse_existing_tool": (1, "Review existing tool before buying"),
    "escalate_manual_review": (2, "Escalate to manual review"),
    "request_clarification": (3, "Request clarification from requester"),
}
BLOCKING_FLAGS = {
    "missing_information": "request_clarification",
    "vendor_risk_unavailable": "escalate_manual_review",
    "conflicting_vendor_evidence": "escalate_manual_review",
    "budget_insufficient": "escalate_manual_review",
    "budget_unverified": "escalate_manual_review",
}


def floor_recommendation(flags: list[str]) -> str:
    codes = [BLOCKING_FLAGS[f] for f in flags if f in BLOCKING_FLAGS] or ["route_for_review"]
    return max(codes, key=lambda c: RECOMMENDATIONS[c][0])
