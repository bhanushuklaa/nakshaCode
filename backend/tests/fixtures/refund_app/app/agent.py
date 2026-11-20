from anthropic import Anthropic

from .tools import check_order_history, read_past_tickets, read_refund_policy, track_delivery

client = Anthropic()


def investigate_claim(order: dict, reason: str) -> str:
    """Let the model investigate the claim using tools."""
    context = {
        "policy": read_refund_policy(),
        "history": check_order_history(order["customer_id"]),
        "tickets": read_past_tickets(order["customer_id"]),
        "delivery": track_delivery(order["tracking_id"]),
    }
    reply = client.messages.create(model="claude-sonnet", messages=[{"role": "user", "content": str(context)}])
    return reply.content[0].text
