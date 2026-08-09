from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import settings


@dataclass
class ClaudeResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


class ClaudeClient:
    """Thin wrapper over the Anthropic SDK with prompt caching for system prompts.

    Optional. Only used if ANTHROPIC_API_KEY is set. The default workflow is
    offline-first: assemble prompts locally, print to stdout, let the user paste
    into Claude Code / Claude.ai / Grok. This class exists so the codebase
    extends cleanly if an API key becomes available later.

    The system prompt is marked as a cache breakpoint so repeated calls (same style
    codex, same format library) reuse the cached prefix and drop per-call cost ~90%.
    """

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or settings.anthropic_api_key
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Use offline mode: the CLI will print "
                "the assembled prompt to stdout for you to paste into Claude Code or Grok."
            )
        from anthropic import Anthropic  # lazy import so offline mode doesn't need the lib installed
        self._client = Anthropic(api_key=key)

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.8,
        cache_system: bool = True,
    ) -> ClaudeResponse:
        model = model or settings.ideation_model
        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": system,
                **({"cache_control": {"type": "ephemeral"}} if cache_system else {}),
            }
        ]
        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_blocks,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        usage = resp.usage
        return ClaudeResponse(
            text=text,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> Any:
        """Request JSON, extract the first JSON block from the response."""
        hardened_system = system + (
            "\n\nReturn ONLY valid JSON. No prose, no markdown fences, no explanation."
        )
        resp = self.complete(
            system=hardened_system,
            user=user,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = resp.text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip().rstrip("```").strip()
        return json.loads(text)
