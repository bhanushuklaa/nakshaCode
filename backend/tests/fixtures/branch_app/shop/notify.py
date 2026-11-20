import smtplib

from slack_sdk import WebClient

slack = WebClient(token="x")


async def email_customer(order):
    smtplib.SMTP("localhost").sendmail("shop", order["email"], "Thanks!")


async def post_to_slack(order):
    slack.chat_postMessage(channel="#orders", text="New order")


def page_on_call(order):
    slack.chat_postMessage(channel="#oncall", text="Payment failure")
