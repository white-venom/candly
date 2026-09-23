from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from candly.alerts.briefs import post_market_review, pre_market_brief
from candly.alerts.jobs import send_test_alert

router = APIRouter(tags=["alerts"])


@router.post("/alerts/test")
def test_alert(request: Request) -> dict:
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(415, "send a JSON request")
    return send_test_alert()


@router.get("/alerts/preview")
def preview(kind: Literal["pre_market", "post_market"]) -> dict:
    build = pre_market_brief if kind == "pre_market" else post_market_review
    return {"kind": kind, "text": build()}
