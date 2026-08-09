"""Prompt linter — catches AI-slop vocabulary and structural weaknesses
before a prompt ever reaches Grok.

A prompt passes the linter only if:
- No banned superlatives ("cinematic", "epic", etc.)
- Names a lens OR a film stock OR a specific lighting setup
- States a time of day / lighting condition
- States a composition cue

Rules are intentionally opinionated; if you disagree, edit them here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


BANNED_SUPERLATIVES = (
    "cinematic",
    "epic",
    "stunning",
    "beautiful",
    "masterpiece",
    "4k",
    "8k",
    "ultra-realistic",
    "ultra realistic",
    "hyperrealistic",
    "hyper-realistic",
    "photorealistic masterpiece",
    "award-winning",
    "award winning",
    "trending on artstation",
    "breathtaking",
    "majestic",
    "ethereal",
    "high detail",
    "highly detailed",
    "intricate details",
    "perfect composition",
    "professional photography",
)


LENS_HINTS = (
    "mm",
    "f/",
    "f ",  # e.g. "f 1.4"
    "aperture",
    "focal length",
    "prime",
    "telephoto",
    "wide-angle",
    "anamorphic",
    "macro lens",
)

FILM_STOCK_HINTS = (
    "portra",
    "cinestill",
    "ektar",
    "ektachrome",
    "fuji 400h",
    "fuji 400",
    "kodak gold",
    "tri-x",
    "ilford",
    "velvia",
    "provia",
    "medium format",
    "large format",
    "technicolor",
    "kodachrome",
    "vhs",
    "super 8",
    "super 16",
)

LIGHTING_HINTS = (
    "window light",
    "softbox",
    "hard light",
    "soft light",
    "backlit",
    "rim light",
    "side light",
    "overhead",
    "practical",
    "fluorescent",
    "tungsten",
    "neon",
    "golden hour",
    "blue hour",
    "overcast",
    "noon sun",
    "firelight",
    "candlelight",
    "moonlight",
    "monitor glow",
    "phone glow",
    "street light",
    "sodium vapor",
)

TIME_OR_CONDITION_HINTS = (
    "morning",
    "dawn",
    "sunrise",
    "midday",
    "noon",
    "afternoon",
    "dusk",
    "sunset",
    "twilight",
    "night",
    "midnight",
    "3am",
    "4am",
    "rainy",
    "fog",
    "foggy",
    "misty",
    "snow",
    "humid",
    "dry",
    "storm",
    "clear sky",
    "overcast",
)

COMPOSITION_HINTS = (
    "rule of thirds",
    "centered",
    "symmetrical",
    "leading lines",
    "negative space",
    "close-up",
    "medium shot",
    "wide shot",
    "over the shoulder",
    "dutch angle",
    "low angle",
    "high angle",
    "eye level",
    "bird's eye",
    "worm's eye",
    "foreground subject",
    "shallow depth",
    "deep focus",
)


@dataclass
class LintIssue:
    severity: str  # "error" | "warn"
    message: str


@dataclass
class LintResult:
    ok: bool
    errors: list[LintIssue] = field(default_factory=list)
    warnings: list[LintIssue] = field(default_factory=list)
    hits: dict[str, bool] = field(default_factory=dict)  # which craft categories were satisfied

    def as_text(self) -> str:
        lines = []
        verdict = "PASS" if self.ok else "FAIL"
        lines.append(f"[{verdict}] prompt lint")
        for i in self.errors:
            lines.append(f"  ERROR: {i.message}")
        for i in self.warnings:
            lines.append(f"  warn:  {i.message}")
        if self.hits:
            lines.append(
                "  craft: "
                + ", ".join(f"{k}={'OK' if v else 'MISSING'}" for k, v in self.hits.items())
            )
        return "\n".join(lines)


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    low = text.lower()
    return any(n in low for n in needles)


def _find_banned(text: str) -> list[str]:
    low = text.lower()
    found = []
    for b in BANNED_SUPERLATIVES:
        # word-boundary match so "4k" doesn't match inside URLs and "beautiful"
        # doesn't match a legitimate "beauty dish" reference.
        pat = r"\b" + re.escape(b) + r"\b"
        if re.search(pat, low):
            found.append(b)
    return found


def lint(prompt: str, *, kind: str = "image") -> LintResult:
    """Run the linter over a prompt string.

    kind: "image" | "motion". Motion prompts additionally require a named camera move.
    """
    result = LintResult(ok=True)

    banned = _find_banned(prompt)
    for b in banned:
        result.errors.append(LintIssue("error", f"banned word: '{b}'"))

    has_lens = _contains_any(prompt, LENS_HINTS)
    has_stock = _contains_any(prompt, FILM_STOCK_HINTS)
    has_lighting = _contains_any(prompt, LIGHTING_HINTS)
    has_time = _contains_any(prompt, TIME_OR_CONDITION_HINTS)
    has_comp = _contains_any(prompt, COMPOSITION_HINTS)

    result.hits = {
        "lens_or_stock": has_lens or has_stock,
        "lighting": has_lighting,
        "time_or_condition": has_time,
        "composition": has_comp,
    }

    if not (has_lens or has_stock):
        result.warnings.append(
            LintIssue("warn", "no named lens/film-stock — consider adding one (e.g. '35mm f/1.4', 'Cinestill 800T')")
        )
    if not has_lighting:
        result.warnings.append(
            LintIssue("warn", "no named lighting setup — consider adding one (e.g. 'hard directional window light')")
        )
    if not has_time:
        result.warnings.append(
            LintIssue("warn", "no time-of-day/condition — consider adding one (e.g. 'dusk', 'overcast', '3am neon')")
        )
    if not has_comp:
        result.warnings.append(
            LintIssue("warn", "no composition cue — consider adding one (e.g. 'rule of thirds, subject left')")
        )

    if kind == "motion":
        moves = (
            "push-in", "push in", "pull-out", "pull out", "dolly", "rack focus",
            "orbit", "lockoff", "lock-off", "ken burns", "whip pan", "tilt",
            "crane", "truck", "handheld", "steadicam",
        )
        if not _contains_any(prompt, moves):
            result.errors.append(
                LintIssue("error", "motion prompt must name ONE camera move (push-in, orbit, rack focus, lockoff, Ken Burns, whip pan, tilt...)")
            )

    result.ok = len(result.errors) == 0
    return result
