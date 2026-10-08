from __future__ import annotations

import json
from pathlib import Path
from fastapi import FastAPI, HTTPException

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "data" / "vendor_risk.json").read_text(encoding="utf-8"))

app = FastAPI(title="FDE Mock Vendor Risk API", version="1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/vendor-risk/{vendor_name:path}")
def vendor_risk(vendor_name: str) -> dict:
    # Starlette already percent-decodes the path; decoding again corrupted names containing '%'.
    # ':path' lets names containing '/' reach the handler instead of 404-ing at the router.
    name = vendor_name
    record = DATA.get(name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No vendor-risk record for '{name}'")
    if record.get("force_error"):
        raise HTTPException(status_code=503, detail=record.get("error_message", "Vendor-risk service unavailable"))
    return {"vendor_name": name, **record}
