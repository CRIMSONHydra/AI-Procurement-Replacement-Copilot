from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from src import data_access, policy
from src.agents import model_chain
from src.solution import handle_request

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "runs" / "decision_log.jsonl"
ACTIONS = {
    "Send to approvers": "Forwards the evidence package to the listed approvers. Nothing is purchased or approved yet.",
    "Return to requester for clarification": "Sends the missing-information questions back to the requester; the request leaves the approval path.",
    "Escalate to manual review": "Holds the request for Procurement/Security manual review of the flagged issues.",
    "Reject request": "Closes the request. The requester is told why and may resubmit.",
}
STYLE = {
    "route_for_review": st.success, "reuse_existing_tool": st.info,
    "escalate_manual_review": st.warning, "request_clarification": st.error,
}

st.set_page_config(page_title="Procurement Request Copilot", layout="wide")
st.session_state.setdefault("results", {})


def md(text: object) -> str:
    """Escape data/LLM text for st.markdown: '$' would start LaTeX math, and request text is
    untrusted, so it must not render links, images, HTML or emphasis."""
    return re.sub(r"([\\`*_\[\]<>$|~])", r"\\\1", str(text))


def label(code: str | None) -> str:
    return policy.RECOMMENDATIONS[code][1] if code in policy.RECOMMENDATIONS else (code or "")


def when(iso: str) -> str:
    """Stored as UTC ISO for the audit log; shown in the reviewer's local time."""
    return datetime.fromisoformat(iso).astimezone().strftime("%d %b %Y, %H:%M")


def read_log() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def api_ok() -> bool:
    try:
        return requests.get(os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001") + "/health", timeout=1).ok
    except requests.RequestException:
        return False


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Settings")
    architecture = st.radio("Architecture", ["single", "staged"], horizontal=True,
                            help="single = one tool-using agent; staged = analyst agent -> policy/risk reviewer agent")
    reviewer = st.text_input("Reviewer name", value="Procurement reviewer")
    st.divider()
    st.caption("System status")
    has_key = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    st.write(("🟢" if has_key else "🔴") + f" LLM: {' → '.join(model_chain()) if has_key else 'no API key - deterministic checks only'}")
    st.write(("🟢" if api_ok() else "🔴") + " Vendor-risk API" + ("" if api_ok() else " unreachable - vendor evidence will be marked unverified"))

st.title("AI Procurement Request Copilot")
st.caption("The copilot gathers evidence and recommends. **Humans approve.** It never purchases, approves spend, or changes budgets.")

# ---------------------------------------------------------------- queue
reqs = data_access.load_requests()
if not reqs:
    st.info("No open purchase requests. The queue is clear.")
    st.stop()
employees = data_access.load_employees().set_index("employee_id")
log = read_log()
last_action = {e["request_id"]: e["action"] for e in log}
queue = pd.DataFrame([{
    "Request": r["request_id"],
    "Requester": employees.loc[r["requester_id"], "name"] if r["requester_id"] in employees.index else "unknown",
    "Department": employees.loc[r["requester_id"], "department"] if r["requester_id"] in employees.index else "unknown",
    "Product": r["product_name"],
    "Annual cost": f"${r['annual_cost_usd']:,.0f}" if r.get("annual_cost_usd") is not None else "missing",
    "Urgency": r.get("urgency"),
    "Copilot": label((st.session_state.results.get((r["request_id"], architecture)) or {}).get("recommendation_code")) or "not analyzed",
    "Human decision": last_action.get(r["request_id"], "pending"),
} for r in reqs])

st.subheader("Request queue")
st.dataframe(queue, hide_index=True, width="stretch")
by_id = {r["request_id"]: r for r in reqs}
rid = st.selectbox("Open request", list(by_id), index=None, placeholder="Select a request to review",
                   format_func=lambda i: f"{i} · {by_id[i]['product_name']}")
if rid is None:
    st.info("Select a request to review it.")
    st.stop()
req = by_id[rid]
st.divider()

# ---------------------------------------------------------------- review screen
left, mid, right = st.columns([0.9, 1.15, 1.05], gap="large")

with left:
    st.subheader(f"{rid} · {req['product_name']}")
    c1, c2 = st.columns(2)
    c1.metric("Annual cost", f"${req['annual_cost_usd']:,.0f}" if req.get("annual_cost_usd") is not None else "missing")
    c2.metric("Users", req.get("user_count") or "missing")
    st.markdown(f"**Vendor:** {md(req['vendor_name'])}  \n**Category:** {md(req['category'])}  \n"
                f"**Data access:** {md(req.get('data_access_level') or 'missing')}  \n"
                f"**Integrations:** {md(', '.join(req.get('requested_integrations') or []) or 'none')}  \n"
                f"**Urgency:** {md(req.get('urgency'))}")
    st.caption("Business justification - requester text, treated as untrusted data")
    st.markdown(f"> {md(req.get('business_justification') or 'missing')}")
    if st.button("Run copilot analysis", type="primary", width="stretch"):
        with st.spinner(f"Gathering evidence ({architecture})..."):
            try:
                st.session_state.results[(rid, architecture)] = handle_request(rid, architecture).model_dump()
            except Exception as exc:  # error state: show it, never hide the request
                st.error(f"Analysis failed: {type(exc).__name__}: {exc}")
        st.rerun()

result = st.session_state.results.get((rid, architecture))

with mid:
    st.subheader("Evidence")
    if not result:
        st.info("Run the analysis to gather evidence from the budget, catalog, vendor-risk and policy tools.")
    else:
        for source in dict.fromkeys(e["source"] for e in result["evidence"]):
            with st.container(border=True):
                st.markdown(f"**{source}**")
                for e in result["evidence"]:
                    if e["source"] == source:
                        st.markdown(f"- {md(e['finding'])}")
                        if e.get("reference"):
                            st.caption(f" Ref: {md(e['reference'])}")

with right:
    st.subheader("Recommendation")
    if not result:
        st.info("No recommendation yet.")
    else:
        STYLE.get(result["recommendation_code"], st.info)(md(result["recommendation"]))
        st.markdown("**Approvals required**  \n" + " · ".join(f"`{a}`" for a in result["required_approvals"]))
        st.markdown("**Risk flags**  \n" + (" ".join(f"`{f}`" for f in result["risk_flags"]) or "none"))
        if result["missing_information"]:
            st.markdown("**Missing information**")
            for m in result["missing_information"]:
                st.markdown(f"- {md(m)}")
        st.markdown(f"**Next step**  \n{md(result['next_step'])}")
        t = result.get("telemetry") or {}
        with st.expander("Run details (telemetry + guardrail)"):
            st.write(f"Architecture **{t.get('architecture')}** · model `{t.get('model')}` · "
                     f"{t.get('latency_ms', 0) / 1000:.1f}s ({(t.get('quota_wait_ms') or 0) / 1000:.1f}s rate-limit wait) · "
                     f"{t.get('llm_calls')} LLM calls · {t.get('tool_calls')} tool calls")
            st.write("Tools called: " + (", ".join(t.get("tool_names") or []) or "none"))
            corr = t.get("guardrail_corrections") or []
            st.write("Guardrail corrections: " + ("none - agent output already met the policy floor" if not corr else ""))
            for c in corr:
                st.markdown(f"- {md(c)}")

# ---------------------------------------------------------------- human decision
if result:
    st.divider()
    st.subheader("Human decision")
    a1, a2 = st.columns([1, 2])
    action = a1.radio("Action", list(ACTIONS))
    a2.info(ACTIONS[action] + (f"  \nApprovers: {', '.join(result['required_approvals'])}" if action == "Send to approvers" else ""))
    reason = a2.text_area("Reason (required - recorded in the audit log)",
                          placeholder="e.g. Agree with copilot; Security to confirm repository controls first.")
    if a2.button(f"Record: {action}", type="primary", disabled=not reason.strip()):
        LOG.parent.mkdir(exist_ok=True)
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "request_id": rid,
                 "reviewer": reviewer, "action": action, "reason": reason.strip(),
                 "copilot_recommendation": result["recommendation_code"], "architecture": architecture,
                 "followed_copilot": {"route_for_review": "Send to approvers", "request_clarification": "Return to requester for clarification",
                                      "escalate_manual_review": "Escalate to manual review"}.get(result["recommendation_code"]) == action}
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        st.success(f"Recorded '{action}' for {rid}. Logged to runs/decision_log.jsonl.")
    history = [e for e in read_log() if e["request_id"] == rid]
    if history:
        st.caption("Decision history")
        st.dataframe(pd.DataFrame([{
            "When": when(e["timestamp"]), "Reviewer": e["reviewer"], "Action": e["action"], "Reason": e["reason"],
            "Copilot recommended": label(e["copilot_recommendation"]),
        } for e in history]), hide_index=True)
