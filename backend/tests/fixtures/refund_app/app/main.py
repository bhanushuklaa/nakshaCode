"""Refund service: receives refund requests and routes them for approval."""
from fastapi import FastAPI

from .agent import investigate_claim
from .db import lookup_order
from .notify import ask_for_approval
from .parsing import parse_verdict

app = FastAPI()


@app.post("/refunds")
def refund_requested(payload: dict):
    """Handle a refund request webhook."""
    order = lookup_order(payload["order_id"])
    verdict = investigate_claim(order, payload["reason"])
    decision = parse_verdict(verdict)
    ask_for_approval(order, decision)
    return {"status": "pending"}


@app.get("/health")
def health():
    return {"ok": True}
