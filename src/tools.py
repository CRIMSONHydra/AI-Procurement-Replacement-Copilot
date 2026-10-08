"""Evidence tools exposed to the agents. All are deterministic code (no LLM).

Tools that touch money, thresholds or policy take `request_id` and read the
system of record themselves, so the model cannot pass in a wrong (or injected)
cost, department or approval status. Only catalog search takes model-chosen
keywords, because "could an existing tool cover this need?" is a semantic call.
"""
from __future__ import annotations

import logging

import pandas as pd
import requests

from src import data_access, policy, vendor_client

log = logging.getLogger(__name__)


def _rows(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(df.notna(), None).to_dict("records")


class ToolBox:
    """One instance per run: holds the per-run vendor-API cache and tool-call telemetry."""

    def __init__(self) -> None:
        self.tool_calls: list[str] = []
        self._vendor_cache: dict[str, dict] = {}

    # ---- agent entry point -------------------------------------------------
    def call(self, name: str, args: dict) -> dict:
        self.tool_calls.append(name)
        fn = getattr(self, name, None)
        if name not in TOOL_NAMES or fn is None:
            return {"error": f"unknown tool '{name}'"}
        try:
            return fn(**args)
        except KeyError as exc:
            return {"error": f"not found: {exc}"}
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}

    # ---- tools ---------------------------------------------------------------
    def get_requester_profile(self, request_id: str) -> dict:
        req = data_access.get_request(request_id)
        employees = {r["employee_id"]: r for r in _rows(data_access.load_employees())}
        emp = employees.get(req.get("requester_id"))
        if emp is None:
            return {"source": "employees.csv", "found": False, "requester_id": req.get("requester_id")}
        manager = employees.get(emp.get("manager_id"))
        return {
            "source": "employees.csv",
            "found": True,
            "employee_id": emp["employee_id"],
            "department": emp["department"],
            "level": emp["level"],
            "manager_id": emp.get("manager_id"),
            "manager_name": manager["name"] if manager else None,
        }

    def check_budget(self, request_id: str) -> dict:
        req = data_access.get_request(request_id)
        profile = self.get_requester_profile(request_id)
        cost = req.get("annual_cost_usd")
        dept = profile.get("department")
        budgets = {r["department"]: r for r in _rows(data_access.load_budgets())}
        row = budgets.get(dept)
        out = {"source": "department_budgets.csv", "as_of": str(policy.reference_date()), "department": dept,
               "annual_cost_usd": cost}
        if row is None:
            return {**out, "status": "unverified",
                    "finding": f"No software budget record for department '{dept}'; budget cannot be verified"}
        available = float(row["available_usd"])
        out["available_usd"] = available
        if cost is None:
            return {**out, "status": "not_checked", "finding": "Annual cost missing; budget check not possible"}
        if cost > available:
            return {**out, "status": "insufficient",
                    "finding": f"${cost:,.0f} exceeds {dept} available budget ${available:,.0f} (shortfall ${cost - available:,.0f})"}
        return {**out, "status": "within_budget",
                "finding": f"${cost:,.0f} is within {dept} available budget ${available:,.0f}"}

    def search_software_catalog(self, request_id: str, keywords: list[str] | None = None) -> dict:
        req = data_access.get_request(request_id)
        history = _rows(data_access.load_purchase_history())
        cat = str(req.get("category") or "").lower()
        vendor = str(req.get("vendor_name") or "").lower()
        terms = [k.lower() for k in (keywords or []) if k and len(k) > 2]
        matches = []
        for item in _rows(data_access.load_software_catalog()):
            if cat and item["category"].lower() == cat:
                match = "same_category"
            elif vendor and item["vendor_name"].lower() == vendor:
                match = "same_vendor"
            elif terms and any(t in f"{item['product_name']} {item['category']} {item['notes']}".lower() for t in terms):
                match = "keyword"
            else:
                continue
            purchases = [p for p in history if p["product_name"] == item["product_name"]]
            matches.append({**item, "match": match, "purchases": purchases})
        return {"source": "software_catalog.csv", "matches": matches}

    def get_vendor_status(self, vendor_name: str) -> dict:
        if vendor_name in self._vendor_cache:
            return self._vendor_cache[vendor_name]
        ref = policy.reference_date()
        registry = next((r for r in _rows(data_access.load_vendors()) if r["vendor_name"] == vendor_name), None)

        api, api_ok, api_error = None, True, None
        try:
            api = vendor_client.get_vendor_risk(vendor_name)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                api = None  # service up, but no assessment on file
            else:
                api_ok, api_error = False, f"HTTP {exc.response.status_code if exc.response is not None else '?'}: {exc}"
        except requests.RequestException as exc:
            api_ok, api_error = False, f"{type(exc).__name__}: service unreachable"
        if not api_ok:
            log.warning("vendor-risk unavailable for %s: %s", vendor_name, api_error)

        reg_status = str((registry or {}).get("security_status") or "missing").lower()
        reg_date = (registry or {}).get("security_review_date")
        api_status = str((api or {}).get("security_review_status") or ("unavailable" if not api_ok else "no_record")).lower()
        api_date = (api or {}).get("last_review_date")

        conflicts = []
        if registry and api:
            if (reg_status == "approved") != (api_status == "approved"):
                conflicts.append(f"registry security_status '{registry['security_status']}' vs vendor-risk service '{api_status}'")
            if reg_date and api_date and str(reg_date) != str(api_date):
                conflicts.append(f"registry review date {reg_date} vs vendor-risk service {api_date}")

        dates = [d for d in (reg_date, api_date) if d]
        expired = api_status == "expired" or any(not policy.review_is_current(d, ref) for d in dates)
        current = (api_ok and reg_status == "approved" and api_status == "approved"
                   and policy.review_is_current(reg_date, ref) and policy.review_is_current(api_date, ref))
        if current:
            summary = f"current (reviewed {api_date}, valid {policy.SECURITY_REVIEW_VALID_DAYS} days)"
        elif not api_ok:
            summary = "cannot be verified - vendor-risk service unavailable"
        elif expired:
            summary = f"expired (last review {api_date or reg_date}, older than {policy.SECURITY_REVIEW_VALID_DAYS} days at {ref})"
        else:
            summary = f"missing or not completed (registry '{reg_status}', service '{api_status}')"

        result = {
            "source": "vendors.csv + vendor-risk API",
            "as_of": str(ref),
            "vendor_name": vendor_name,
            "in_registry": registry is not None,
            "is_new_vendor": registry is None or registry.get("procurement_status") == "New",
            "procurement_status": (registry or {}).get("procurement_status"),
            "legal_terms_status": (registry or {}).get("legal_terms_status") or "Unknown",
            "registry_security_status": (registry or {}).get("security_status"),
            "registry_review_date": reg_date,
            "registry_notes": (registry or {}).get("notes"),
            "risk_api_available": api_ok,
            "risk_api_error": api_error,
            "risk_api_status": api_status,
            "risk_api_review_date": api_date,
            "risk_level": (api or {}).get("risk_level"),
            "processes_personal_data": (api or {}).get("processes_personal_data"),
            "stores_data_outside_region": (api or {}).get("stores_data_outside_region"),
            "risk_api_notes": (api or {}).get("notes"),
            "assessment_current": current,
            "review_expired": expired,
            "assessment_summary": summary,
            "conflicts": conflicts,
        }
        self._vendor_cache[vendor_name] = result
        return result

    def evaluate_policy_rules(self, request_id: str) -> dict:
        req = data_access.get_request(request_id)
        profile = self.get_requester_profile(request_id)
        result = policy.evaluate(
            req,
            profile if profile["found"] else None,
            self.check_budget(request_id),
            self.get_vendor_status(str(req.get("vendor_name") or "")),
            self.search_software_catalog(request_id)["matches"],
        )
        return {"source": "policy engine (procurement_policy.md v2026.09)", **result}


TOOL_NAMES = ["get_requester_profile", "check_budget", "search_software_catalog", "get_vendor_status", "evaluate_policy_rules"]

_REQ = {"request_id": {"type": "string", "description": "Purchase request ID, e.g. REQ-1001"}}
TOOL_SPECS = [
    {"name": "get_requester_profile",
     "description": "Look up the requester's employee record: department, level, manager.",
     "parameters": {"type": "object", "properties": _REQ, "required": ["request_id"]}},
    {"name": "check_budget",
     "description": "Deterministic budget check: compares the request's annual cost with the requester department's available software budget.",
     "parameters": {"type": "object", "properties": _REQ, "required": ["request_id"]}},
    {"name": "search_software_catalog",
     "description": "Search the approved software catalog (with purchase history) for tools that overlap the request. "
                    "Automatically matches same category and same vendor; add keywords describing the need to find functional overlap in other categories.",
     "parameters": {"type": "object", "properties": {
         **_REQ, "keywords": {"type": "array", "items": {"type": "string"}, "description": "Need-describing keywords, e.g. ['templates','design']"}},
         "required": ["request_id"]}},
    {"name": "get_vendor_status",
     "description": "Vendor evidence: internal registry + live vendor-risk API, with deterministic assessment freshness (365 days) and registry-vs-API conflict detection.",
     "parameters": {"type": "object", "properties": {"vendor_name": {"type": "string"}}, "required": ["vendor_name"]}},
    {"name": "evaluate_policy_rules",
     "description": "Deterministic policy engine: approval thresholds, required Security/Privacy/Legal reviews, missing required fields, "
                    "prompt-injection scan. Its approvals and flags are the minimum and cannot be removed.",
     "parameters": {"type": "object", "properties": _REQ, "required": ["request_id"]}},
]
