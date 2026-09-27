"""Portfolio-level risk status - the guardrails the investor skill requires
stay intact, surfaced as one call so the UI can render them as a panel."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from .. import convert, deps
from ..schemas import RiskStatusOut

router = APIRouter(prefix="/api", tags=["risk"], dependencies=[Depends(deps.require_api_key)])


@router.get("/risk", response_model=RiskStatusOut)
def get_risk() -> RiskStatusOut:
    client = deps.get_alpaca_client()
    state = deps.load_state()
    risk = deps.get_risk_engine(state)

    try:
        account = client.get_account()
        positions = client.get_positions()
    except deps.engine.AlpacaAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return convert.risk_status_to_out(account, risk, positions)
