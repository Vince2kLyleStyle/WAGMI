"""Variant generator — take one winning prompt and produce N deliberate
variations exploring a single axis at a time.

Designed so the operator can run a batch of 6-12 Grok generations from a single
winner and see exactly what changed across variants, rather than random re-rolls.
"""
from __future__ import annotations

from dataclasses import dataclass

from .prompt_engine import load_codex


VARIANT_SYSTEM = """You are a variant designer. You receive ONE winning prompt
that produced a great Grok output. Your job: generate N variants, each changing
exactly ONE axis from a defined taxonomy. Variants must stay in the same format
and aesthetic family — no genre hops.

## Variant axes (pick from these)

1. TIME_OF_DAY       — dawn / golden hour / noon / dusk / 3am / midnight / overcast
2. LENS              — 24mm / 35mm / 50mm / 85mm / 135mm / anamorphic
3. FILM_STOCK        — Portra 400 / Cinestill 800T / Fuji 400H / Ektachrome / Tri-X B&W
4. LIGHTING          — hard window / soft overcast / practical neon / firelight / single softbox
5. COMPOSITION       — rule of thirds / centered / low angle / dutch / over-shoulder
6. SUBJECT_WARDROBE  — swap wardrobe only
7. LOCATION          — swap setting, keep subject + action
8. MOOD              — tender / cold / defiant / exhausted / absurd

## Rules

- Each variant changes EXACTLY ONE axis from the seed prompt.
- Keep the rest of the prompt verbatim where possible.
- Do NOT introduce banned words (cinematic, epic, stunning, 4k, masterpiece...).
- Label each variant with its axis.

## Output

Return ONLY JSON:

{
  "variants": [
    {"axis": "LENS", "change": "swapped 85mm f/1.2 -> 35mm f/1.4", "prompt": "..."},
    {"axis": "LIGHTING", "change": "swapped hard window light -> practical neon", "prompt": "..."}
  ],
  "suggested_batch_order": ["axis1", "axis2", ...],
  "note": "one sentence of editorial guidance"
}
"""


@dataclass
class VariantBrief:
    system: str
    user: str


def build_variant_brief(winner_prompt: str, n_variants: int = 6, axes: list[str] | None = None) -> VariantBrief:
    axes = axes or ["TIME_OF_DAY", "LENS", "FILM_STOCK", "LIGHTING", "COMPOSITION", "MOOD"]
    user = (
        "## Seed prompt\n"
        f"{winner_prompt.strip()}\n\n"
        f"## N variants\n{n_variants}\n\n"
        f"## Axes to vary\n{', '.join(axes)}\n\n"
        "## Style codex\n"
        f"{load_codex() or '(empty)'}\n\n"
        "## Task\nProduce the variants per the system rules. JSON only."
    )
    return VariantBrief(system=VARIANT_SYSTEM, user=user)
