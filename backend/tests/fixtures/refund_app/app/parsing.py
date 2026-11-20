import json


def parse_verdict(text: str) -> dict:
    """Turn the model's reply into a structured decision."""
    return json.loads(text)
