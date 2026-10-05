"""FastAPI app: chat endpoint (NDJSON event stream), dashboard endpoint, static frontend."""
from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from backend.agent.agent import MODEL, get_session, llm_available, run_turn  # noqa: E402
from backend.analytics import engine  # noqa: E402
from backend.analytics.charts import build_chart  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
app = FastAPI(title="Asset Vantage — Financial Analyst Agent")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


@app.post("/api/chat")
def chat(req: ChatRequest):
    session = get_session(req.session_id)

    def stream():
        yield json.dumps({"type": "session", "session_id": session.id}) + "\n"
        for event in run_turn(session, req.message):
            yield json.dumps(event, default=str, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


class RechartRequest(BaseModel):
    session_id: str
    result_id: str
    chart_type: str


@app.post("/api/rechart")
def rechart(req: RechartRequest):
    """Let the user flip a chart to another allowed type without another LLM call."""
    from backend.agent.tools import run_tool

    session = get_session(req.session_id)
    content, spec, is_error = run_tool(session.ctx, "render_chart",
                                       {"result_id": req.result_id, "chart_type": req.chart_type})
    return {"ok": not is_error, "spec": spec, "error": content if is_error else None}


@app.get("/api/overview")
def overview():
    """Data for the always-visible dashboard rail."""
    health = engine.get_health_score()
    summary = engine.get_financial_summary()
    cash = engine.get_cashflow("last_12_months")
    return {
        "mode": "llm" if llm_available() else "unconfigured",
        "model": MODEL if llm_available() else None,
        "summary": summary,
        "health": build_chart("get_health_score", health, "gauge"),
        "cashflow": build_chart("get_cashflow", cash, "combo", "Last 12 months"),
        "assets": build_chart("get_assets", engine.get_assets(), "doughnut", "Assets"),
        "quality": {k: v for k, v in engine.get_data_quality_report().items() if k != "issues"},
        "upcoming_dues": engine.get_liabilities()["upcoming_dues"],
    }


@app.get("/api/health")
def healthcheck():
    return {"ok": True, "llm": llm_available()}


# Serve the built React app (created by `npm run build` in frontend/) from the same server,
# so after building you only need to run this one Python server.
DIST = FRONTEND / "dist"
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/")
def index():
    if (DIST / "index.html").exists():
        return FileResponse(DIST / "index.html")
    return HTMLResponse("<h3>Frontend not built yet.</h3><p>Run <code>npm install</code> and "
                        "<code>npm run build</code> inside the <code>frontend</code> folder, then refresh. "
                        "(Or use <code>npm run dev</code> and open http://localhost:5173.)</p>")
