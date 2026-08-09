"""Brief quality scorer — 0-100 score for a production prompt.

A brief scores highly when it:
- Has zero banned words
- Has every craft coverage category (lens/film, lighting, time, composition)
- Ends with an explicit negative list
- Names a specific emotion or action (not just a noun)
- For motion prompts: names exactly one camera move

The scorer is heuristic — it correlates with quality, doesn't guarantee it.
Use it as a gate before pasting into Grok: if it's under 70, revise first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .linter import (
    BANNED_SUPERLATIVES,
    COMPOSITION_HINTS,
    FILM_STOCK_HINTS,
    LENS_HINTS,
    LIGHTING_HINTS,
    TIME_OR_CONDITION_HINTS,
    _contains_any,
    _find_banned,
)


@dataclass
class ScoreResult:
    score: int                       # 0-100
    issues: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    subscores: dict[str, int] = field(default_factory=dict)

    def as_text(self) -> str:
        grade = "A" if self.score >= 85 else "B" if self.score >= 70 else "C" if self.score >= 55 else "D"
        lines = [f"score: {self.score}/100  grade: {grade}"]
        lines.append("subscores:")
        for k, v in self.subscores.items():
            lines.append(f"  {k:<18} {v:>3}")
        if self.strengths:
            lines.append("strengths:")
            for s in self.strengths:
                lines.append(f"  + {s}")
        if self.issues:
            lines.append("issues:")
            for i in self.issues:
                lines.append(f"  - {i}")
        return "\n".join(lines)


# Heuristic vocab for additional scoring dimensions
ACTION_HINTS = (
    " is ", " does ", " doing ", " holds", " lighting", " about to",
    " turns", " runs", " walking", " looking", " reaching", " drinking",
    " eating", " reading", " writing", " staring", " laughing", " crying",
    " tossing", " catching", " dropping",
)

EMOTION_HINTS = (
    "tired", "exhausted", "anxious", "hopeful", "defiant", "tender",
    "cold", "angry", "content", "confident", "resigned", "surprised",
    "amused", "focused", "bored", "curious", "contemplative", "smug",
    "grateful", "lonely", "proud", "determined", "playful", "stoic",
)


def _has_negative_list(prompt: str) -> bool:
    """Detect an explicit 'no X, no Y' style negative tail."""
    # Look for 2+ 'no ' phrases in the last third of the prompt
    tail = prompt[max(0, len(prompt) - max(200, len(prompt) // 3)):].lower()
    return tail.count("no ") >= 2


def _word_count(prompt: str) -> int:
    return len(re.findall(r"\w+", prompt))


def score(prompt: str, *, kind: str = "image") -> ScoreResult:
    """Score a prompt 0-100. Higher is shippable."""
    result = ScoreResult(score=0)

    # --- Subscore 1: banned words (35 points possible, -35 per hit max) ---
    banned = _find_banned(prompt)
    banned_score = max(0, 35 - len(banned) * 20)
    result.subscores["banned_words"] = banned_score
    if banned:
        result.issues.append(f"banned words present: {', '.join(banned)}")
    else:
        result.strengths.append("no banned superlatives")

    # --- Subscore 2: craft coverage (30 points, 7.5 per category) ---
    lens_or_stock = _contains_any(prompt, LENS_HINTS) or _contains_any(prompt, FILM_STOCK_HINTS)
    lighting = _contains_any(prompt, LIGHTING_HINTS)
    time_condition = _contains_any(prompt, TIME_OR_CONDITION_HINTS)
    composition = _contains_any(prompt, COMPOSITION_HINTS)
    covered = sum([lens_or_stock, lighting, time_condition, composition])
    craft_score = int(covered * 7.5)
    result.subscores["craft_coverage"] = craft_score
    if covered == 4:
        result.strengths.append("all craft categories named (lens/stock, lighting, time, composition)")
    else:
        missing = []
        if not lens_or_stock: missing.append("lens/film-stock")
        if not lighting: missing.append("lighting")
        if not time_condition: missing.append("time/condition")
        if not composition: missing.append("composition")
        result.issues.append(f"missing craft cues: {', '.join(missing)}")

    # --- Subscore 3: action or emotion named (10 points) ---
    has_action = _contains_any(prompt, ACTION_HINTS)
    has_emotion = _contains_any(prompt, EMOTION_HINTS)
    action_score = 10 if (has_action or has_emotion) else 0
    result.subscores["action_or_emotion"] = action_score
    if action_score == 10:
        result.strengths.append("names a specific action or emotion")
    else:
        result.issues.append("no specific action or emotion — subject may read as generic")

    # --- Subscore 4: explicit negative list (10 points) ---
    neg_score = 10 if _has_negative_list(prompt) else 0
    result.subscores["negative_list"] = neg_score
    if neg_score == 10:
        result.strengths.append("ends with an explicit negative list")
    else:
        result.issues.append("no explicit 'no X, no Y' negative tail")

    # --- Subscore 5: length sweet spot (10 points) ---
    wc = _word_count(prompt)
    if 40 <= wc <= 150:
        length_score = 10
        result.strengths.append(f"length in sweet spot ({wc} words)")
    elif 25 <= wc < 40 or 150 < wc <= 200:
        length_score = 6
        result.issues.append(f"length {wc} words — a bit {'short' if wc < 40 else 'long'}")
    else:
        length_score = 2
        result.issues.append(f"length {wc} words — well outside 40-150 sweet spot")
    result.subscores["length"] = length_score

    # --- Subscore 6: motion-specific (5 points for motion; N/A for image) ---
    if kind == "motion":
        moves = (
            "push-in", "push in", "pull-out", "pull out", "dolly", "rack focus",
            "orbit", "lockoff", "lock-off", "ken burns", "whip pan", "tilt",
            "crane", "truck", "handheld", "steadicam", "static",
        )
        if _contains_any(prompt, moves):
            motion_score = 5
            result.strengths.append("names a specific camera move")
        else:
            motion_score = 0
            result.issues.append("motion prompt missing a named camera move")
        result.subscores["camera_move"] = motion_score
    else:
        motion_score = 5  # full credit for image prompts (no move needed)
        result.subscores["camera_move"] = 5

    total = banned_score + craft_score + action_score + neg_score + length_score + motion_score
    # Cap at 100
    result.score = min(100, total)
    return result


__all__ = ["ScoreResult", "score"]
