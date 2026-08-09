"""Reverse engineer — given an image you love (screenshot, saved post, reference),
produce the brief you'd paste into Grok to re-create its *look* (not the subject).

Image analysis is done by Claude Code (in this session) or Claude.ai — they have
vision. The module's job is to assemble the SYSTEM + USER prompt that instructs
Claude to describe the image with the right craft vocabulary.
"""
from __future__ import annotations

from pathlib import Path


REVERSE_SYSTEM = """You are a photography / cinematography analyst. You are
shown a single image. Your job: describe it in terms that a prompt engineer
can use to recreate the LOOK (not the specific subject) in Grok.

## Required output fields

Return ONLY JSON:

{
  "look_description": "one paragraph, camera-first vocabulary",
  "estimated_lens": "e.g. '35mm f/1.4' or 'wide, likely 24mm'",
  "estimated_film_stock_or_sensor_look": "e.g. 'Cinestill 800T halation' or 'digital medium format'",
  "lighting": "named setup, not adjectives",
  "time_of_day_or_condition": "...",
  "color_palette": ["hex1", "hex2", "hex3", "hex4"],
  "composition": "rule-name + where the subject is placed",
  "mood_dials": {"warmth": 0.0, "contrast": 0.0, "grain": 0.0, "desaturation": 0.0},
  "recreate_prompt": "a production-grade prompt ready for Grok that would produce this LOOK with any subject",
  "banned_words_check": "confirm the recreate_prompt uses NONE of: cinematic, epic, stunning, masterpiece, 4k, 8k, award-winning"
}

## Rules

- NEVER use the banned superlatives in recreate_prompt.
- NAME the lens, film stock, lighting by name.
- State composition by rule name.
- If uncertain about a specific value, say 'estimated' — don't hallucinate.
"""


def build_reverse_brief(image_path: str | Path, context: str = "") -> tuple[str, str]:
    """Assemble the SYSTEM + USER prompt for Claude to analyze an image.

    The USER message references the image by absolute path so whoever runs this
    (Claude Code session) can open/inspect it locally.
    """
    user = (
        f"## Image to analyze\npath: {Path(image_path).resolve()}\n\n"
        + (f"## Operator context\n{context.strip()}\n\n" if context else "")
        + "## Task\n"
        "Open the image. Produce the JSON described in the system prompt.\n"
        "If you cannot access the image, respond with "
        '{"error": "cannot access image at <path>"}.'
    )
    return REVERSE_SYSTEM, user
