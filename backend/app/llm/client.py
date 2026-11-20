"""Minimal client for any OpenAI-compatible server (vLLM, Ollama, llama.cpp).

Responses are constrained to a JSON schema when the server supports it, so the
model cannot return broken JSON. If the server rejects `json_schema`, we fall
back to plain JSON mode and validate the result ourselves.
"""

import json
import logging
import re
from typing import Any

import httpx

from ..config import Settings

log = logging.getLogger(__name__)
FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._http = httpx.Client(
            base_url=settings.llm_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=settings.llm_timeout_s,
        )
        self._schema_supported = True

    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled

    def is_available(self) -> bool:
        if not self.enabled:
            return False
        try:
            return self._http.get("/models", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def chat_json(self, messages: list[dict[str, str]], schema: dict[str, Any],
                  name: str, max_tokens: int | None = None) -> dict[str, Any]:
        """Send a chat request and return the parsed JSON object."""
        body: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
        }
        if self._schema_supported:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema, "strict": True},
            }
        else:
            body["response_format"] = {"type": "json_object"}

        try:
            response = self._http.post("/chat/completions", json=body)
            if response.status_code == 400 and self._schema_supported:
                log.info("Server rejected json_schema; falling back to json_object mode")
                self._schema_supported = False
                return self.chat_json(messages, schema, name, max_tokens)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        content = response.json()["choices"][0]["message"].get("content") or ""
        return parse_json(content)


def parse_json(content: str) -> dict[str, Any]:
    """Parse model output, tolerating code fences or text around the object."""
    text = FENCE.sub("", content.strip())
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("Model did not return JSON.") from None
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("Model returned JSON that is not an object.")
    return value
