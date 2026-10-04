"""
api/main.py

Dashboard/control API for the NDHD Alpaca trading engine.

Run (from the repository root, venv activated):

    uvicorn api.main:app --host 127.0.0.1 --port 8000

Swagger UI:  http://127.0.0.1:8000/docs
ReDoc:       http://127.0.0.1:8000/redoc
Dashboard:   http://127.0.0.1:8000/  (serves web/, see web/README.md)

This process is READ-MOSTLY by default and runs independently of the
trading engine (portfolio_engine.py's own poll loop) - see
.github/skills/ndhd-trading-developer/SKILL.md's "Repository map" for why
that separation matters. It shares the same state file and calls the same
Alpaca account, so what you see here reflects the same ground truth the
trading engine acts on (with the fast tier refreshed every few seconds and
the heavier strategy/indicator tier on ~POLL_SECONDS-scale cadence).

Authentication: set DASHBOARD_API_KEY in the environment and send it as
the X-API-Key header. If unset, this API runs with NO authentication -
fine for 127.0.0.1-only local development, not safe to expose further.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import deps, logs, ws
from .routers import alerts, congress, control, market, risk, strategy

LOGGER = logging.getLogger("dashboard-api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

WS_FAST_INTERVAL_SECONDS = float(os.getenv("WS_FAST_INTERVAL_SECONDS", "4"))
WS_SLOW_EVERY_N_TICKS = int(os.getenv("WS_SLOW_EVERY_N_TICKS", "8"))

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WEB_DIR = _REPO_ROOT / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("DASHBOARD_API_KEY"):
        LOGGER.warning(
            "DASHBOARD_API_KEY is not set - this API (including order "
            "submission and the kill switch) is running WITHOUT "
            "authentication. Only safe if bound to 127.0.0.1."
        )
    task = __import__("asyncio").create_task(
        ws.broadcaster_loop(WS_FAST_INTERVAL_SECONDS, WS_SLOW_EVERY_N_TICKS)
    )
    LOGGER.info(
        "Dashboard API starting. Paper=%s LiveTrading=%s state_file=%s",
        deps.engine.CONFIG.paper,
        deps.engine.CONFIG.live_trading,
        deps.engine.CONFIG.state_file,
    )
    yield
    task.cancel()


app = FastAPI(
    title="NDHD Trading Dashboard API",
    description=(
        "Read/control API over the NDHD Alpaca trading engine "
        "(portfolio_engine.py). See /docs for Swagger, /redoc for ReDoc."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

_default_origins = "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173"
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router)
app.include_router(strategy.router)
app.include_router(risk.router)
app.include_router(alerts.router)
app.include_router(control.router)
app.include_router(congress.router)
app.include_router(congress.ledger_router)


@app.get("/api/health", tags=["health"])
def health() -> dict:
    cfg = deps.engine.CONFIG
    alpaca_configured = bool(os.getenv("ALPACA_API_KEY")) and bool(os.getenv("ALPACA_SECRET_KEY"))
    return {
        "ok": True,
        "paper": cfg.paper,
        "live_trading": cfg.live_trading,
        "state_file": cfg.state_file,
        "alpaca_credentials_configured": alpaca_configured,
        "authenticated_api": bool(os.getenv("DASHBOARD_API_KEY")),
    }


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    expected_key = os.getenv("DASHBOARD_API_KEY")
    if expected_key and websocket.query_params.get("api_key") != expected_key:
        await websocket.close(code=4401)
        return

    await ws.manager.connect(websocket)
    try:
        # Send an immediate full snapshot so the UI doesn't sit blank
        # until the next scheduled tick.
        from starlette.concurrency import run_in_threadpool

        await websocket.send_json(await run_in_threadpool(ws.build_fast_snapshot))
        await websocket.send_json(await run_in_threadpool(ws.build_strategy_snapshot))
        backlog = await run_in_threadpool(logs.read_tail, 200)
        if backlog:
            await websocket.send_json({"type": "log", "entries": backlog, "backlog": True})

        while True:
            await websocket.receive_text()  # client sends nothing meaningful; just detect disconnect
    except WebSocketDisconnect:
        pass
    finally:
        ws.manager.disconnect(websocket)


if _WEB_DIR.exists():
    if not (_WEB_DIR / "dist" / "app.js").exists():
        LOGGER.warning("web/dist/ is not built - the dashboard page will be blank. "
                       "Build it: cd web; npm ci; npm run build (see web/README.md).")
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="dashboard")
else:
    LOGGER.warning("web/ directory not found at %s - dashboard UI will not be served.", _WEB_DIR)
