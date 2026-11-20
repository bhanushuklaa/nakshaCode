import asyncio

from fastapi import FastAPI, HTTPException

from .repo import find_order, reserve_stock, save_payment
from .notify import email_customer, page_on_call, post_to_slack
from .payments import charge, PaymentError

app = FastAPI()


@app.post("/checkout")
async def checkout(order_id: str):
    order = find_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    try:
        receipt = charge(order)
    except PaymentError:
        page_on_call(order)
        raise HTTPException(status_code=402, detail="Payment failed")
    for item in order["items"]:
        reserve_stock(item)
    save_payment(receipt)
    await asyncio.gather(email_customer(order), post_to_slack(order))
    return {"ok": True}
