"""Account, positions, and orders - thin read-only pass-throughs to Alpaca."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import convert, deps
from ..schemas import AccountOut, OrderOut, PositionOut

router = APIRouter(prefix="/api", tags=["market"], dependencies=[Depends(deps.require_api_key)])


@router.get("/account", response_model=AccountOut)
def get_account(client=Depends(deps.get_alpaca_client)) -> AccountOut:
    try:
        account = client.get_account()
    except deps.engine.AlpacaAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return convert.account_to_out(account)


@router.get("/positions", response_model=List[PositionOut])
def get_positions(client=Depends(deps.get_alpaca_client)) -> List[PositionOut]:
    try:
        positions = client.get_positions()
    except deps.engine.AlpacaAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return [convert.position_to_out(p) for p in positions]


@router.get("/orders", response_model=List[OrderOut])
def get_orders(
    status_filter: str = Query(default="open", alias="status", description="open | closed | all"),
    client=Depends(deps.get_alpaca_client),
) -> List[OrderOut]:
    try:
        orders = client.get_orders(status=status_filter)
    except deps.engine.AlpacaAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return [convert.order_to_out(o) for o in orders]
