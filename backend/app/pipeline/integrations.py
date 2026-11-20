"""Detect external services (Postgres, Slack, OpenAI, ...) from imported packages."""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

CATALOG_FILE = Path(__file__).with_name("integrations.yaml")


@dataclass(frozen=True)
class Integration:
    id: str
    label: str
    category: str


@lru_cache(maxsize=1)
def _catalog() -> tuple[dict[str, Integration], list[tuple[str, str]]]:
    entries = yaml.safe_load(CATALOG_FILE.read_text())
    by_id: dict[str, Integration] = {}
    prefixes: list[tuple[str, str]] = []
    for entry in entries:
        item = Integration(entry["id"], entry["label"], entry["category"])
        by_id[item.id] = item
        prefixes.extend((pkg.lower(), item.id) for pkg in entry["packages"])
    prefixes.sort(key=lambda p: len(p[0]), reverse=True)  # longest match first
    return by_id, prefixes


def match_package(package: str) -> Integration | None:
    """Return the integration for an import like "slack_sdk.web" or "@slack/web-api"."""
    by_id, prefixes = _catalog()
    name = package.lower()
    for prefix, integration_id in prefixes:
        if name == prefix or name.startswith((prefix + ".", prefix + "/")):
            return by_id[integration_id]
    return None


def find_by_name(text: str) -> Integration | None:
    """Loose lookup used for LLM output and dataset conversion, e.g. "slack" or "Postgres DB".

    Matches whole words only, so "author" does not match "auth".
    """
    by_id, _ = _catalog()
    needle = f" {re.sub(r'[^a-z0-9]+', ' ', text.lower())} "
    for item in by_id.values():
        label = re.sub(r"[^a-z0-9]+", " ", item.label.lower()).strip()
        if f" {item.id} " in needle or (label and f" {label} " in needle):
            return item
    return None


def all_integrations() -> list[Integration]:
    return list(_catalog()[0].values())
