# Architecture Decision Memo

## Decision
**Ship Architecture A, the single agent**, behind the deterministic policy guardrail.

## Evidence
Same 14 cases for both: the 6 public cases, 4 other sample requests, and 4 synthetic edge cases. Model `gemini-3.1-flash-lite`, temperature 0, 2 runs per case, two independent eval runs, so 56 samples per architecture (`evals/results/`).

| Metric | Rules only | **Single** | Staged |
|---|---:|---:|---:|
| Correct next action | 12/14 | **56/56** | 48/55 |
| Policy followed | 13/14 | **56/56** | 55/55 |
| Escalation correct | 13/14 | **56/56** | 55/55 |
| Policy failures found manually | – | **0** | 4 |
| Median latency (run 1 / run 2) | <0.1s | 15.4s / **10.0s** | 15.1s / 25.1s |
| LLM calls / tool calls | 0 / 0 | **2** / 5 | 3 / 5 |

Grounding: none of the 181 LLM-written evidence items cited a tool that wasn't called, and every dollar amount and date in them traces to the data.

## Trade-offs
Staged improved nothing measurable. Both always kept the policy floor, so the guardrail never had to correct either.

What staged made worse:
- **Correctness.** The reviewer decides from the analyst's summary and over-weighted catalog overlap.
  - EXT-01, 4 of 4 samples: it told Customer Success to reuse NeuralDesk Business for customer PII. That tool is approved "only for permitted data classes", so this breaks policy §8.
  - EXT-03, 3 of 4 samples: it missed that a company-wide TaskFlow already covers the need.
- **Cost.** 50% more LLM calls, about 166 instead of 250 analyses a day on the free tier.
- **Latency.** Equal in run 1, 2.5x slower in run 2.
- **More to break.** Another prompt and handoff schema. Its only failed run was a 503 on the extra call.

Reliability came from the code, not from agent count. Rules alone get every threshold, flag and escalation right. The LLM adds the two judgments code can't make: "an existing tool covers this" (EXT-03) and "this purpose is too vague" (SYN-04). One agent with tools makes them best.

## Risks / limitations
- 56 samples is enough to compare architectures, not to certify accuracy. Before production: 100+ labelled historical requests, plus tracking reviewer agreement in the decision log.
- The injection scan is a regex, so paraphrased attacks rely on the LLM. Approvals stay safe because code computes them.
- Only one model was tested. Re-run `evals/compare.py` before changing models.
- The guardrail made zero corrections here. It is proven by unit tests, not yet by production traffic.

## Why this is the right MVP
The problem is evidence gathering plus a little judgment on top of fixed rules. Rules live in code, and humans own approvals. Under that split, the single agent matched or beat staged on every quality metric, at two-thirds of the LLM cost, with less to maintain. A second agent would be worth adding only if policy required independent review of the evidence, and that should first be tried as a deterministic check.
