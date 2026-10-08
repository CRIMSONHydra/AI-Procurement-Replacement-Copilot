# Functional spec: Procurement Request Copilot (MVP)

## User
A procurement reviewer working through a queue of employee software and service purchase requests.

## Goal
Cut the manual evidence gathering for each request (budget, existing tools, vendor status, policy) down to one review screen. The screen should end in a recommended next action that the reviewer accepts or overrides, with a recorded reason. Sensitive decisions stay with humans.

## Acceptance criteria
1. For any request ID, the system returns a `ProcurementDecision` with these fields: recommendation, evidence, required approvals, missing information, risk flags, next step, and `human_review_required = true`.
2. Approval thresholds, budget checks, the 365-day vendor review window, Security/Privacy/Legal triggers and required-field checks come from code, not the LLM, and the boundary cases are unit-tested.
3. Every evidence item names the tool that produced it. Items citing a tool that wasn't called are dropped.
4. If material evidence is missing, unavailable or conflicting, the output says so and the recommendation becomes clarification or escalation. It is never treated as favorable.
5. Instructions embedded in request or vendor text never change approvals or flags, and they are flagged as `prompt_injection_detected`.
6. If the LLM fails, the system still returns the deterministic decision, flagged `llm_unavailable`, instead of crashing.
7. The UI shows the request, the evidence and the recommendation. Recording a human action requires a reason and writes it to an audit log.
8. Both architectures run on the same eval set. The comparison reports correctness, grounding, policy adherence, escalation, latency, and LLM/tool call counts.

## Out of scope (this MVP)
- Purchasing, approving, notifying approvers, or editing budgets. The copilot only recommends.
- Submitting new requests through the UI. `requests.json` is the system of record.
- Authentication and roles, and a production database for the audit log.

## Open questions for the client
- Who owns the overlap rule? Is "same category" the right trigger, or should Procurement keep a curated list of functional equivalents?
- Should requests with an unknown data-access level go back to the requester first, or straight to Security?
- What reviewer agreement rate with the copilot would justify routing low-risk requests (≤ $1,000, no sensitive data) on a fast path?
