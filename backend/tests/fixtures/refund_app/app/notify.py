from slack_sdk import WebClient

slack = WebClient(token="x")


def ask_for_approval(order: dict, decision: dict) -> None:
    slack.chat_postMessage(channel="#refunds", text=f"Approve refund for {order['id']}? {decision}")
