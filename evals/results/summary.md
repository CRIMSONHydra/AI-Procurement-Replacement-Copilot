# Architecture comparison

Model: `gemini-3.1-flash-lite` | runs per LLM case: 2 | policy reference date: 2026-09-30 | generated 2026-10-08 18:26

`rules` = deterministic floor only (no LLM) - the reference that shows what the agents add.
Rows where every model failed (LLM fell back to rules) are excluded from LLM columns and counted separately.

| Metric | rules | single | staged |
|---|---:|---:|---:|
| Correct next action | 12/14 | 28/28 | 23/27 |
| Policy followed (final output) | 13/14 | 28/28 | 27/27 |
| Policy followed by agent alone (pre-guardrail) | 13/14 | 28/28 | 27/27 |
| Evidence grounded (no fabricated sources) | 14/14 | 28/28 | 27/27 |
| Human escalation correct | 13/14 | 28/28 | 27/27 |
| Public minimum checks | 6/6 | 12/12 | 11/11 |
| Avg latency ms (end-to-end) | 60 | 12,320 | 27,025 |
| Avg latency ms (net of rate-limit waits) | 60 | 8,407 | 24,584 |
| Avg LLM calls | 0.0 | 2.0 | 3.0 |
| Avg tool calls | 0.0 | 5.0 | 5.0 |
| Guardrail corrections (total) | 0 | 0 | 0 |
| LLM runs failed -> rules fallback | 0 | 0 | 1 |

## Per case

| Case | rules | single | staged |
|---|---|---|---|
| PUB-01 | route_for_review | route_for_review, route_for_review | route_for_review, route_for_review |
| PUB-02 | route_for_review | route_for_review, route_for_review | route_for_review, route_for_review |
| PUB-03 | route_for_review | route_for_review, route_for_review | route_for_review, route_for_review |
| PUB-04 | escalate_manual_review | escalate_manual_review, escalate_manual_review | escalate_manual_review, escalate_manual_review |
| PUB-05 | request_clarification | request_clarification, request_clarification | request_clarification, request_clarification |
| PUB-06 | escalate_manual_review | escalate_manual_review, escalate_manual_review | escalate_manual_review, escalate_manual_review |
| EXT-01 | route_for_review | route_for_review, route_for_review | reuse_existing_tool ✗, reuse_existing_tool ✗ |
| EXT-02 | escalate_manual_review | escalate_manual_review, escalate_manual_review | escalate_manual_review, escalate_manual_review |
| EXT-03 | route_for_review ✗ | reuse_existing_tool, reuse_existing_tool | route_for_review ✗, route_for_review ✗ |
| EXT-04 | route_for_review | route_for_review, route_for_review | route_for_review, route_for_review |
| SYN-01 | route_for_review | route_for_review, route_for_review | reuse_existing_tool, reuse_existing_tool |
| SYN-02 | escalate_manual_review | escalate_manual_review, escalate_manual_review | escalate_manual_review, escalate_manual_review |
| SYN-03 | route_for_review | route_for_review, route_for_review | route_for_review, route_for_review |
| SYN-04 | route_for_review ✗ | request_clarification, request_clarification | request_clarification, request_clarification |
