# AI Procurement Request Copilot

An internal copilot for software and service purchase requests. It gathers evidence (budget, existing tools, vendor risk, policy), applies the procurement rules in code, and recommends the next action. **Every decision stays with a human.**

> All companies, vendors, employees, prices and policies are synthetic assessment data.

- **User:** a procurement reviewer working a queue of purchase requests.
- **Decision they make:** send the request to approvers, return it for clarification, escalate it, or reject it.
- **What the copilot produces:** recommendation · evidence · approvals required · missing information · risk flags · next step (the `ProcurementDecision` contract in `src/contracts.py`).

## Quick start

Requires Python 3.11+ and a free [Google AI Studio](https://aistudio.google.com/apikey) key.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env                 # Windows: Copy-Item .env.example .env, then set GEMINI_API_KEY
python verify_setup.py               # expect PRE-FLIGHT PASSED
python run_local.py                  # one command: vendor-risk API :8001 + UI :8501
```

Open http://127.0.0.1:8501. Pick a request, click **Run copilot analysis**, review the evidence, then record a decision with a reason.

Without an API key the app still runs on the deterministic checks alone and flags `llm_unavailable`.

### Tests and evaluations

```bash
python -m unittest discover -s tests                         # 18 tests, no API key needed
python -m uvicorn mock_api.app:app --port 8001               # starter harness needs the API running (or use run_local.py)
python evals/run_public_evals.py --architecture single       # starter harness (6 public cases)
python evals/run_public_evals.py --architecture staged
python evals/compare.py --model gemini-3.1-flash-lite --rpm 12 --runs 2   # full A vs B comparison
```

`compare.py` starts the mock API if needed. It writes `evals/results/comparison.csv` (one row per run, with the starter's evaluation-template columns: correct_next_action, grounded_evidence, policy_followed, human_escalation_correct, latency, LLM/tool calls), `decisions.jsonl` (full outputs for manual review) and `summary.md`. If the API is not running, the starter harness doesn't fail. The copilot correctly reports vendor risk as unverifiable and escalates, so PUB-01 then fails its "no `vendor_risk_unavailable`" check.

## Product workflow

1. **Queue.** All requests with cost, urgency, copilot status and human decision status.
2. **Review screen.** Three panels:
   - **Request details.** The requester's text is labelled as untrusted data.
   - **Evidence.** Grouped by the tool that produced each fact, with record IDs and policy sections.
   - **Recommendation.** Approvals, risk flags, missing information, next step, plus run telemetry and any guardrail corrections.
3. **Human decision.** Choose an action, see its consequence, and enter a **required reason**. The decision is appended to `runs/decision_log.jsonl` along with whether the reviewer followed the copilot.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the workflow diagram, tool table, guardrail rules and the full assumptions list. The functional spec (user, goal, acceptance criteria, out of scope) is in [docs/SPEC.md](docs/SPEC.md).

- **AI interprets and recommends.** It understands the need, judges overlap with existing tools and how credible the stated gap is, judges whether the purpose is clear enough, and chooses the next action.
- **Code enforces the rules.** Thresholds, budget, review freshness, vendor conflicts, required fields, Security/Privacy/Legal triggers and the injection scan all live in `src/policy.py` and `src/tools.py`.
- **Humans approve.** The copilot has no tool that can approve, buy or change a budget.

| | A: Single agent | B: Staged (2 agents) |
|---|---|---|
| Agents | Procurement Agent (tools + decision) | Procurement Analyst (tools, evidence pack only), then Policy/Risk Reviewer (no tools, decides from the pack + raw tool results) |
| Shared | 5 tools, Gemini function calling (`mode=ANY`, temperature 0), the same deterministic guardrail | same |

### Tools (all deterministic code; the LLM decides when to call them)
`get_requester_profile`, `check_budget`, `search_software_catalog`, `get_vendor_status` (registry + live vendor-risk API), `evaluate_policy_rules` (policy engine).

### Reliability controls
- **Policy floor.** The LLM can add approvals and flags but never remove them, and can never make the recommendation less cautious.
- **Grounding.** Evidence citing a tool that wasn't called is dropped.
- **Untrusted data.** Request text and API notes are wrapped as data and scanned for injection. Tools read numbers from the system of record, not from model arguments.
- **Failures.** If the vendor API is down, the result is "unverified" and escalated, never assumed favorable. The API client retries only transient errors. If the LLM fails, the decision falls back to the deterministic one. Models fall back per run (`MODEL_NAME` then `MODEL_FALLBACKS`), with optional `LLM_RPM` pacing.

### Starter-pack issues found and fixed
| Issue | Fix |
|---|---|
| `run_local.py` hung on Streamlit's first-run email prompt | `--server.headless true` |
| Mock API decoded vendor names twice and 404'd names containing `/` | removed the extra `unquote`; `{vendor_name:path}` |
| `vendor_client` had no retry; one 503 failed the lookup | retries transient errors (408/429/5xx/connection) only |
| `.gitignore` excluded eval result CSVs, which are a deliverable | removed the rule; results are committed |
| Employee E007's department "Go To Market" has no budget row | handled as `budget_unverified`, Finance review and escalation, instead of a crash |
| Null cost or user count (REQ-1006) would break naive budget/threshold code | treated as missing information; threshold can't be computed, so Procurement triages |
| `gemini-2.5-flash` (an obvious default) is unavailable to new keys | default `gemini-3.1-flash-lite` (free tier 500 req/day; `gemini-3.8-flash` allows only 20/day) with a per-run fallback chain |

## Assumptions

The full list, with reasons, is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#assumptions). The ones that most affect outcomes:

- **Dates.** They come from the policy's reference date (2026-09-30), not the system clock. A vendor review is current for 365 days, inclusive.
- **Thresholds.** Financial tiers use `> $10,000` (policy §4), but Legal review for a new vendor starts at `>= $10,000` (§7), exactly as the policy words it.
- **Overlap.** A catalog tool in the same category raises `existing_tool_overlap`. Whether the team should actually reuse that tool is the LLM's judgment.
- **Unknown means unverified.** If the vendor API is unavailable, or a data-access level or department budget is unknown, the copilot never treats that as favorable. It escalates or asks for clarification.
- **Conflicts.** If the internal registry and the vendor-risk API disagree, that is a conflict for Security to resolve, not something the copilot picks a side on.
- **Human review is always required.** Every request ends with a human decision; the copilot only recommends.

## Evaluation results

The same 14 cases were run through all three architectures:

- the 6 public cases;
- the 4 other sample requests (EXT);
- 4 synthetic edge cases (SYN), stored in `evals/synthetic_requests.json` so `data/` stays untouched: injection telling the copilot to skip Security, a department with no budget record, an unknown vendor, and a vague purpose.

Settings: model `gemini-3.1-flash-lite` pinned with no fallback, temperature 0, and 2 runs per case. The whole eval was run **twice independently**: [run 1](evals/results/run1_summary.md) and [run 2](evals/results/summary.md).

| Metric (both eval runs combined) | Rules only | **A: Single** | B: Staged |
|---|---:|---:|---:|
| Correct next action | 12/14 | **56/56** | 48/55 |
| Policy followed (approvals + flags, final output) | 13/14 | **56/56** | 55/55 |
| Policy followed by the agent alone (before the guardrail) | n/a | **56/56** | 55/55 |
| Human escalation correct | 13/14 | **56/56** | 55/55 |
| Evidence grounded (no fabricated sources) | 14/14 | **56/56** | 55/55 |
| Policy failures found manually | n/a | **0** | 4 |
| Same answer on repeated runs | n/a | **28/28 case-runs** | 26/28 |
| Median latency, run 1 / run 2 | <0.1s | 15.4s / 10.0s | 15.1s / 25.1s |
| Avg LLM calls / tool calls | 0 / 0 | **2 / 5** | 3 / 5 |
| Starter public harness | 6/6 | 6/6 (`evals/results_single.csv`) | 6/6 (`evals/results_staged.csv`) |

What the rows mean:
- **Rules only** is the deterministic floor with no LLM. It gives the same output every run, so it is counted once. It misses exactly the two cases that need judgment, EXT-03 and SYN-04, which shows what the LLM adds.
- **Staged, 55 samples:** one staged run hit a Gemini 503 and safely fell back to the rules decision, which was still correct. That run is excluded.
- **Manual review:** I read all 181 LLM-written evidence items. A script also checked that every dollar amount and date in them exists in the data or is computable from it (review date + 365, budget shortfall); none failed.
- **Staged's manual policy failure:** EXT-01, in all 4 samples. It recommended reusing NeuralDesk Business for customer-PII ticket drafting even though the tool is approved "only for permitted data classes", which violates policy §8.
- **Staged's other misses:** EXT-03, 3 of 4 samples, where it missed that the company-wide TaskFlow covers the need.
- **Guardrail corrections were 0 for both architectures:** the agents always called `evaluate_policy_rules` and kept its floor. The guardrail's behaviour is covered by `tests/test_policy.py`.

## Ship decision

**Ship Architecture A (single agent).** It matched or beat the staged variant on every quality metric. It uses two-thirds of the LLM calls (about 250 instead of 166 analyses a day on the free tier) and has one less prompt and handoff to maintain. Reliability comes from the deterministic floor and the human gate, not from adding agents. The full reasoning is in [docs/DECISION_MEMO.md](docs/DECISION_MEMO.md), 480 words.

## Known limitations
- The injection scan is a regex heuristic. Paraphrased attacks rely on the LLM (still behind the floor), which is why `prompt_injection_detected` is advisory.
- The overlap floor is exact category match. Functional overlap across categories depends on the LLM's catalog keywords.
- The eval set is 14 cases (6 public, 4 other sample requests, 4 synthetic) with 2 runs each. That's enough to compare architectures, not to certify accuracy.
- Free-tier quotas dominate end-to-end latency. Net latency (minus rate-limit waits) is reported separately.
- There's no auth; the decision log is a local JSONL file.
