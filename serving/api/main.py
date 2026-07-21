from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from fundxray_core.config import settings
from pipelines.enrichment import groq_client
from pipelines.spark.analytics import metrics

from .models.schemas import FeeDragRequest, PortfolioRequest
from .routers import auth_router
from .services import xray as svc

WEB = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(
    title="FundXRay",
    description=("Transparency layer over India's mutual fund industry, built on "
                 "SEBI-mandated public disclosures. Informational only — not "
                 "investment advice."),
    version="0.1.0",
)

app.include_router(auth_router.router)

if (WEB / "static").exists():
    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    f = WEB / "templates" / "index.html"
    return f.read_text(encoding="utf-8") if f.exists() else "<h1>FundXRay</h1>"


@app.get("/health")
def health():
    ok = settings.artifact_path.exists()
    return {"status": "ok" if ok else "artifact_missing",
            "artifact": str(settings.artifact_path),
            "groq_configured": settings.groq_enabled,
            "smartapi_configured": settings.smartapi_enabled}


@app.get("/api/schemes")
def schemes():
    return svc.schemes()


@app.post("/api/xray")
def xray(req: PortfolioRequest):
    try:
        result = svc.xray(req.holdings)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))

    if req.explain:
        result["narrative"] = groq_client.explain(
            {k: result[k] for k in ("summary", "sectors", "overlaps",
                                    "weighted_ter_regular_pct", "as_of")},
            focus="portfolio look-through")
    return result


@app.get("/api/active-share")
def active_share():
    return svc.active_share_table()


@app.get("/api/dtl")
def dtl(limit: int = 25):
    return {"metric": "days_to_liquidate",
            "definition": "MF-held shares / (participation x 30-session ADV)",
            "caveat": ("ADV is backward-looking and contracts precisely when "
                       "liquidity matters most. Use as a relative ranking."),
            "rows": svc.dtl_table(limit)}


@app.get("/api/drift/{scheme_code}")
def drift(scheme_code: str):
    rows = svc.drift(scheme_code)
    if not rows:
        raise HTTPException(404, f"no drift series for {scheme_code}")
    return rows


@app.post("/api/fee-drag")
def fee_drag(req: FeeDragRequest):
    return metrics.fee_drag(req.monthly_contribution, req.years,
                            req.gross_return_pct, req.ter_a_pct, req.ter_b_pct)


@app.get("/api/quality")
def quality():
    return svc.quality()


@app.post("/api/cas")
async def upload_cas(file: UploadFile = File(...), password: str = Form("")):
    """Parse a CAMS/KFintech Consolidated Account Statement.

    The PDF is parsed in memory and never written to disk. PAN, email, address
    and folio numbers are redacted before parsing; only scheme identifiers and
    values are returned. The password is used once and not retained.
    """
    from serving.api.services.cas_parser import parse_bytes

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "CAS must be a PDF")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "file too large (20 MB limit)")
    try:
        result = parse_bytes(data, password or None)
    except Exception as e:
        raise HTTPException(400, f"could not read CAS: {e}")
    finally:
        del data                      # drop the statement from memory promptly

    return {
        "holdings": [{"isin": h.isin, "scheme_name": h.scheme_name,
                      "value": h.value} for h in result.holdings],
        "total_value": result.total_value,
        "warnings": result.warnings,
        "privacy": ("Parsed in memory. Not stored. PAN, email and folio numbers "
                    "were redacted before parsing."),
    }


@app.get("/api/turnover/{scheme_code}")
def turnover(scheme_code: str):
    rows = svc.q("SELECT * FROM turnover WHERE scheme_code = ? "
                 "ORDER BY disclosure_month", [scheme_code]).to_dict("records")
    if not rows:
        raise HTTPException(404, f"no turnover series for {scheme_code}")
    return {"metric": "inferred_turnover",
            "caveat": ("Lower bound. Intra-month round trips are invisible to "
                       "monthly disclosure, and price movement alters weights "
                       "without any trading."),
            "rows": rows}


@app.get("/api/crowding")
def crowding(limit: int = 25):
    return svc.q("SELECT * FROM crowding ORDER BY mf_holding_cr DESC LIMIT ?",
                 [limit]).to_dict("records")


@app.get("/api/meta")
def meta():
    m = svc.meta()
    return {**m, "disclaimer": ("Informational only, not investment advice. "
                                "Built on public SEBI-mandated disclosures.")}
