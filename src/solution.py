from __future__ import annotations

from src.agents import analyze
from src.contracts import Architecture, ProcurementDecision


def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    """Assessment adapter: single = one tool-using agent; staged = analyst agent -> policy/risk reviewer agent.

    Both run through the same deterministic guardrail (src/agents.py::finalize).
    """
    return analyze(request_id, architecture)
