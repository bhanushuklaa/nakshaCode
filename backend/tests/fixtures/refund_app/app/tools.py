import requests
import shopify
from notion_client import Client as Notion
from zenpy import Zenpy

notion = Notion(auth="token")
zendesk = Zenpy(subdomain="shop")


def read_refund_policy() -> str:
    return notion.pages.retrieve("policy-page")


def check_order_history(customer_id: str) -> list:
    return shopify.Order.find(customer_id=customer_id)


def read_past_tickets(customer_id: str) -> list:
    return list(zendesk.search(requester=customer_id))


def track_delivery(tracking_id: str) -> dict:
    return _fetch_tracking(tracking_id)


def _fetch_tracking(tracking_id: str) -> dict:
    return requests.get(f"https://track.example.com/{tracking_id}").json()
