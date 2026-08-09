from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import settings


FORMATS_PATH = settings.data_dir / "formats" / "library.yaml"
PLAYBOOKS_DIR = settings.data_dir / "playbooks"


@dataclass
class Format:
    slug: str
    kind: str
    description: str
    prompt_scaffold: str | None
    prompt_scaffold_still: str | None
    prompt_scaffold_motion: str | None
    good_models: list[str]
    good_models_still: list[str]
    good_models_motion: list[str]
    slot_hints: dict


def load_formats(path: Path = FORMATS_PATH) -> list[Format]:
    data = yaml.safe_load(path.read_text())
    out = []
    for f in data.get("formats", []):
        out.append(
            Format(
                slug=f["slug"],
                kind=f["kind"],
                description=f.get("description", "").strip(),
                prompt_scaffold=f.get("prompt_scaffold"),
                prompt_scaffold_still=f.get("prompt_scaffold_still"),
                prompt_scaffold_motion=f.get("prompt_scaffold_motion"),
                good_models=f.get("good_models", []),
                good_models_still=f.get("good_models_still", []),
                good_models_motion=f.get("good_models_motion", []),
                slot_hints=f.get("slot_hints", {}) or {},
            )
        )
    return out


def load_codex(path: Path = settings.codex_path) -> str:
    if not path.exists():
        return ""
    return path.read_text()


def load_playbook(name: str, playbooks_dir: Path = PLAYBOOKS_DIR) -> str:
    """Load a named playbook (e.g. 'grok-imagine-patterns', 'meme-typography')."""
    path = playbooks_dir / f"{name}.md"
    if not path.exists():
        return ""
    return path.read_text()


def load_relevant_playbooks(format_kind: str, playbooks_dir: Path = PLAYBOOKS_DIR) -> str:
    """Load playbooks relevant to the format kind (image/video) and concatenate."""
    names = ["grok-imagine-patterns"]
    if format_kind == "video":
        names.append("video-img2vid-patterns")
        names.append("music-edit-patterns")
    names += ["meme-typography"]
    out_parts = []
    for n in names:
        txt = load_playbook(n, playbooks_dir)
        if txt:
            out_parts.append(f"### Playbook: {n}\n\n{txt}")
    return "\n\n---\n\n".join(out_parts)


SYSTEM_PROMPT_TEMPLATE = """You are the Director — a prompt engineer and creative
lead for a single operator building a high-craft photo + short video pipeline for
X/Twitter. Your job is to turn rough operator intent into a production-grade
prompt that can be pasted directly into Grok (Nano Banana / Aurora / Grok Imagine)
to generate the asset.

YOU ARE NOT GENERATING IMAGES. You are writing the brief. A real model will
execute it. Your job is to be specific, technical, and craft-literate so the
model has nowhere to hide.

## Hard rules for every prompt you write

1. NEVER use the words: "cinematic", "epic", "stunning", "beautiful", "masterpiece",
   "4k", "8k", "ultra-realistic", "award-winning", "trending on artstation". These
   are meaningless and mark the output as AI slop.
2. NAME the lens, named film stock, named lighting setup, named location cue.
   Specificity over superlatives.
3. State the composition rule (rule of thirds, centered, leading lines, negative
   space left/right, symmetrical).
4. State the time of day and the weather / ambient condition.
5. For video: pick ONE camera move by name (push-in, pull-out, rack focus, orbit,
   lockoff, Ken Burns, whip pan). Never "cinematic camera move".
6. For memes with text in the image: quote the exact text verbatim, tell the
   model the font family (Impact, Helvetica Bold, monospace, serif) and the
   placement (top-center, bottom-center, right-aligned).
7. End each prompt with what NOT to render (e.g. "no extra fingers, no warped
   text, no logo watermarks, no lens flares unless specified").

## Input you'll receive

You'll get:
- The operator's rough intent (1-2 sentences)
- The project's style codex (living doc of what's worked before)
- The format library entry for the chosen format
- Optional: reference library notes

## Output

Return a JSON object with this exact shape:

{
  "format_slug": "<the chosen format slug>",
  "model_route": "<one of the format's good_models, picked for this brief>",
  "prompt": "<the production prompt, ready to paste into Grok>",
  "negative_prompt": "<comma-separated things to exclude>",
  "variants_to_try": [
    "<one-line tweak for variant 2>",
    "<one-line tweak for variant 3>",
    "<one-line tweak for variant 4>"
  ],
  "rationale": "<one paragraph: why these choices given the intent + codex>",
  "post_caption_ideas": [
    "<one sharp X caption option>",
    "<one alternate, different register>",
    "<one alternate, shorter>"
  ],
  "next_move_if_this_lands": "<e.g. 'animate with 4s slow push-in via Grok Imagine' or 'generate matched pair for A/B'>"
}

If the operator asked for video, ALSO include:
  "still_prompt": "<the hero still prompt, executed first>",
  "motion_prompt": "<the img2vid prompt for the still>",
  "duration_sec": <integer 3-6>,
  "camera_move": "<named move>"

Return ONLY the JSON. No prose outside it.
"""


def build_user_message(
    intent: str,
    format_: Format,
    codex: str,
    reference_notes: str = "",
    playbooks: str = "",
) -> str:
    lines = [
        "## Operator intent",
        intent.strip(),
        "",
        "## Chosen format",
        f"slug: {format_.slug}",
        f"kind: {format_.kind}",
        f"description: {format_.description}",
    ]
    if format_.prompt_scaffold:
        lines += ["", "### Prompt scaffold", format_.prompt_scaffold.strip()]
    if format_.prompt_scaffold_still:
        lines += ["", "### Still scaffold", format_.prompt_scaffold_still.strip()]
    if format_.prompt_scaffold_motion:
        lines += ["", "### Motion scaffold", format_.prompt_scaffold_motion.strip()]
    if format_.slot_hints:
        lines += ["", "### Slot hints"]
        for k, v in format_.slot_hints.items():
            lines.append(f"- {k}: {v}")
    models = format_.good_models or (format_.good_models_still + format_.good_models_motion)
    if models:
        lines += ["", "### Eligible models", ", ".join(models)]
    lines += ["", "## Style codex (living doc)"]
    lines.append(codex.strip() or "(empty — first pieces will seed this)")
    if reference_notes:
        lines += ["", "## Reference notes", reference_notes.strip()]
    if playbooks:
        lines += ["", "## Craft playbooks (cite these rules; do not restate)", playbooks]
    lines += [
        "",
        "## Your task",
        "Produce the JSON brief described in the system prompt. One brief, ready to paste.",
        "Your prompt MUST pass the linter: no banned words; name lens/film-stock; name lighting; state time-of-day; state composition.",
    ]
    return "\n".join(lines)


def assemble_offline_prompt(
    intent: str,
    format_slug: str,
    codex_path: Path = settings.codex_path,
    formats_path: Path = FORMATS_PATH,
    reference_notes: str = "",
    include_playbooks: bool = True,
    playbooks_dir: Path = PLAYBOOKS_DIR,
) -> tuple[str, str]:
    """Return (system_prompt, user_message) ready to paste into Claude Code or Claude.ai.

    This is the offline-first path: no API call, just assemble the full brief
    locally and print it. The operator pastes it into any Claude interface they
    already have access to.
    """
    formats = load_formats(formats_path)
    match = next((f for f in formats if f.slug == format_slug), None)
    if match is None:
        available = ", ".join(f.slug for f in formats)
        raise ValueError(f"Unknown format '{format_slug}'. Available: {available}")
    codex = load_codex(codex_path)
    playbooks = load_relevant_playbooks(match.kind, playbooks_dir) if include_playbooks else ""
    user = build_user_message(intent, match, codex, reference_notes, playbooks=playbooks)
    return SYSTEM_PROMPT_TEMPLATE, user
