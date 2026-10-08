"""Run the same case set through rules-only, single-agent and staged architectures and compare.

    python evals/compare.py --model gemini-3.1-flash-lite --rpm 15
    python evals/compare.py --cases PUB-01 SYN-04 --architectures single

Writes evals/results/comparison.csv (per run), decisions.jsonl (full outputs) and summary.md (aggregates).
The mock vendor-risk API is started automatically if it is not already running.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import data_access, policy  # noqa: E402
from src.agents import analyze, finalize  # noqa: E402
from src.tools import ToolBox  # noqa: E402
from evals.run_public_evals import evaluate as public_evaluate  # noqa: E402

OUT = ROOT / "evals" / "results"
BLOCKING = {"escalate_manual_review", "request_clarification"}


def ensure_mock_api() -> subprocess.Popen | None:
    url = os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
    try:
        requests.get(f"{url}/health", timeout=1)
        return None
    except requests.RequestException:
        pass
    port = url.rsplit(":", 1)[-1]
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_api.app:app", "--port", port],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            requests.get(f"{url}/health", timeout=0.5)
            return proc
        except requests.RequestException:
            time.sleep(0.25)
    raise RuntimeError("mock vendor-risk API did not start")


def include_synthetic_requests() -> None:
    """Synthetic edge cases live in evals/, not data/; patch them into the loader for eval runs only."""
    extra = json.loads((ROOT / "evals" / "synthetic_requests.json").read_text(encoding="utf-8"))
    original = data_access.load_requests
    data_access.load_requests = lambda: original() + extra


def score(case: dict, d, public: dict | None) -> dict:
    exp = case["expected"]
    corrections = d.telemetry.guardrail_corrections if d.telemetry else []
    approvals, flags = set(d.required_approvals), set(d.risk_flags)

    def followed(appr: set, fl: set) -> bool:
        ok = set(exp["approvals"]) <= appr and set(exp["flags"]) <= fl
        ok &= not (fl & set(exp.get("forbidden_flags", [])))
        ok &= not (appr & set(exp.get("forbidden_approvals", [])))
        if exp.get("approvals_exact"):
            ok &= appr == set(exp["approvals"])
        return ok

    # Reconstruct what the agent proposed before the guardrail added anything.
    added_a = {c.split("added approval ")[1].split(" (")[0] for c in corrections if c.startswith("added approval")}
    added_f = {c.split("added flag ")[1].split(" (")[0] for c in corrections if c.startswith("added flag")}
    raised = any(c.startswith("recommendation raised") for c in corrections)
    raw_ok = followed(approvals - added_a, flags - added_f) and not raised

    need = [c in BLOCKING for c in exp["recommendation"]]
    escalated = d.recommendation_code in BLOCKING
    esc_ok = d.human_review_required and (escalated if all(need) else (not escalated if not any(need) else True))
    return {
        "correct_next_action": d.recommendation_code in exp["recommendation"],
        "grounded_evidence": not any("ungrounded" in c for c in corrections),
        "policy_followed": followed(approvals, flags),
        "agent_policy_followed_pre_guardrail": raw_ok,
        "human_escalation_correct": esc_ok,
        "public_min_checks": (not public_evaluate(d, public)) if public else "",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--architectures", nargs="+", default=["rules", "single", "staged"], choices=["rules", "single", "staged"])
    ap.add_argument("--cases", nargs="*", help="case_ids to run (default: all)")
    ap.add_argument("--runs", type=int, default=1, help="repeat each LLM case N times to see variance")
    ap.add_argument("--model", help="pin one model and disable fallbacks so architectures are compared like-for-like")
    ap.add_argument("--rpm", type=float, help="client-side LLM requests/minute pacing")
    args = ap.parse_args()
    if args.model:
        os.environ["MODEL_NAME"], os.environ["MODEL_FALLBACKS"] = args.model, ""
    if args.rpm:
        os.environ["LLM_RPM"] = str(args.rpm)

    cases = json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8"))
    if args.cases:
        cases = [c for c in cases if c["case_id"] in args.cases]
    public = {c["case_id"]: c["expectations"] for c in json.loads((ROOT / "evals" / "public_cases.json").read_text(encoding="utf-8"))}
    include_synthetic_requests()
    api = ensure_mock_api()
    rows, decisions = [], []
    try:
        for arch in args.architectures:
            for case in cases:
                for run in range(1 if arch == "rules" else args.runs):
                    start = time.perf_counter()
                    if arch == "rules":
                        d = finalize(case["request_id"], None, ToolBox(), "rules", 0, start, None)
                    else:
                        d = analyze(case["request_id"], arch)
                    t = d.telemetry
                    decisions.append({"case_id": case["case_id"], "architecture": arch, "run": run + 1, **d.model_dump()})
                    s = score(case, d, public.get(case["case_id"]))
                    valid = "llm_unavailable" not in d.risk_flags
                    rows.append({
                        "case_id": case["case_id"], "architecture": arch, "run": run + 1, **s,
                        "latency_ms": t.latency_ms, "net_latency_ms": round(t.latency_ms - (t.quota_wait_ms or 0), 1),
                        "llm_calls": t.llm_calls, "tool_calls": t.tool_calls, "model": t.model or "",
                        "valid_llm_run": valid if arch != "rules" else "",
                        "recommendation_code": d.recommendation_code,
                        "notes": " | ".join(t.guardrail_corrections),
                    })
                    r = rows[-1]
                    print(f"{arch:7} {case['case_id']:7} {d.recommendation_code:24} action={'Y' if r['correct_next_action'] else 'N'} "
                          f"policy={'Y' if r['policy_followed'] else 'N'} raw={'Y' if r['agent_policy_followed_pre_guardrail'] else 'N'} "
                          f"{t.latency_ms/1000:5.1f}s llm={t.llm_calls} tools={t.tool_calls} {'' if valid else 'LLM-FAILED'}", flush=True)
    finally:
        if api:
            api.terminate()

    OUT.mkdir(exist_ok=True)
    with (OUT / "comparison.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    with (OUT / "decisions.jsonl").open("w", encoding="utf-8") as f:  # full outputs, for manual grounding review
        f.writelines(json.dumps(x) + "\n" for x in decisions)
    (OUT / "summary.md").write_text(summarize(rows, args), encoding="utf-8")
    print(f"\nWrote {OUT / 'comparison.csv'} and {OUT / 'summary.md'}")


def summarize(rows: list[dict], args) -> str:
    def pct(rs, k):
        return f"{sum(1 for r in rs if r[k] is True)}/{len(rs)}"

    def avg(rs, k):
        return f"{sum(float(r[k]) for r in rs) / len(rs):,.0f}" if rs else "-"

    def avg1(rs, k):
        return f"{sum(float(r[k]) for r in rs) / len(rs):.1f}" if rs else "-"

    archs = list(dict.fromkeys(r["architecture"] for r in rows))
    lines = [
        "# Architecture comparison",
        "",
        f"Model: `{os.getenv('MODEL_NAME') or 'default chain'}` | runs per LLM case: {args.runs} | "
        f"policy reference date: {policy.reference_date()} | generated {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "`rules` = deterministic floor only (no LLM) - the reference that shows what the agents add.",
        "Rows where every model failed (LLM fell back to rules) are excluded from LLM columns and counted separately.",
        "",
        "| Metric | " + " | ".join(archs) + " |",
        "|---|" + "---:|" * len(archs),
    ]
    by = {a: [r for r in rows if r["architecture"] == a and r["valid_llm_run"] is not False] for a in archs}
    pub = {a: [r for r in by[a] if r["public_min_checks"] != ""] for a in archs}
    metrics = [
        ("Correct next action", lambda a: pct(by[a], "correct_next_action")),
        ("Policy followed (final output)", lambda a: pct(by[a], "policy_followed")),
        ("Policy followed by agent alone (pre-guardrail)", lambda a: pct(by[a], "agent_policy_followed_pre_guardrail")),
        ("Evidence grounded (no fabricated sources)", lambda a: pct(by[a], "grounded_evidence")),
        ("Human escalation correct", lambda a: pct(by[a], "human_escalation_correct")),
        ("Public minimum checks", lambda a: pct(pub[a], "public_min_checks")),
        ("Avg latency ms (end-to-end)", lambda a: avg(by[a], "latency_ms")),
        ("Avg latency ms (net of rate-limit waits)", lambda a: avg(by[a], "net_latency_ms")),
        ("Avg LLM calls", lambda a: avg1(by[a], "llm_calls")),
        ("Avg tool calls", lambda a: avg1(by[a], "tool_calls")),
        ("Guardrail corrections (total)", lambda a: str(sum(len(r["notes"].split(" | ")) for r in by[a] if r["notes"]))),
        ("LLM runs failed -> rules fallback", lambda a: str(sum(1 for r in rows if r["architecture"] == a and r["valid_llm_run"] is False))),
    ]
    for name, fn in metrics:
        lines.append(f"| {name} | " + " | ".join(fn(a) for a in archs) + " |")
    lines += ["", "## Per case", "", "| Case | " + " | ".join(archs) + " |", "|---|" + "---|" * len(archs)]
    for cid in dict.fromkeys(r["case_id"] for r in rows):
        cells = []
        for a in archs:
            rs = [r for r in rows if r["case_id"] == cid and r["architecture"] == a]
            cells.append(", ".join(f"{r['recommendation_code']}{'' if r['correct_next_action'] else ' ✗'}" for r in rs))
        lines.append(f"| {cid} | " + " | ".join(cells) + " |")
    notes = [r for r in rows if r["notes"]]
    if notes:
        lines += ["", "## Guardrail corrections", ""]
        lines += [f"- **{r['architecture']} {r['case_id']} run {r['run']}**: {r['notes']}" for r in notes]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
