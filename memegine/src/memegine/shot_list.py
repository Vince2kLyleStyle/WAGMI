from __future__ import annotations

from .prompt_engine import SYSTEM_PROMPT_TEMPLATE, load_codex


SHOT_LIST_SYSTEM = """You are the Director planning a short (6-12 second) video
piece for X/Twitter from a single operator brief. Output a shot list that a
human (the operator) will execute shot-by-shot in Grok Imagine, then stitch
together with FFmpeg.

## Rules

1. Maximum 3 shots. Two is better. One is often best.
2. Total duration: 3-12 seconds. Assume 9:16 vertical or 1:1 square.
3. For each shot, specify:
   - scene: what the camera sees (concrete, not "cinematic")
   - camera_move: ONE named move (push-in, pull-out, rack focus, orbit, lockoff,
     Ken Burns, whip pan). Never "cinematic move".
   - duration_sec: integer 2-6
   - lens_and_film: e.g. "35mm, Cinestill 800T"
   - lighting: named setup, not "dramatic"
   - still_prompt: the prompt to generate the hero still for this shot
   - motion_prompt: the img2vid prompt to animate that still
4. Between shots: specify the cut type (hard cut, match cut, match-on-action,
   whip transition, J-cut-audio-leads). No crossfades unless justified.
5. Specify whether the piece has audio, and if so whether it's voiceover, music,
   sfx, or ambient. Suggest a brief sound design cue.
6. Write ONE sentence of editorial justification — why this shot list, why this
   cut pattern, given the intent.

## Output

Return JSON only:

{
  "total_duration_sec": <int>,
  "aspect_ratio": "9:16" | "1:1" | "16:9",
  "shots": [
    {
      "index": 1,
      "scene": "...",
      "camera_move": "...",
      "duration_sec": <int>,
      "lens_and_film": "...",
      "lighting": "...",
      "still_prompt": "...",
      "motion_prompt": "..."
    }
  ],
  "cuts": ["hard cut", "match cut"],
  "audio": {
    "has_audio": true,
    "kind": "music" | "voiceover" | "sfx" | "ambient" | "none",
    "cue": "..."
  },
  "x_caption_ideas": ["...", "...", "..."],
  "justification": "one sentence"
}
"""


def build_shot_list_user_message(intent: str, codex: str | None = None) -> str:
    codex = codex if codex is not None else load_codex()
    parts = [
        "## Operator intent",
        intent.strip(),
        "",
        "## Style codex",
        codex.strip() or "(empty — treat as first piece)",
        "",
        "## Task",
        "Plan the shot list per the system rules. JSON only.",
    ]
    return "\n".join(parts)


def assemble_offline_shot_list_prompt(intent: str) -> tuple[str, str]:
    """Return (system, user) for the shot-list task, ready to paste into Claude Code."""
    return SHOT_LIST_SYSTEM, build_shot_list_user_message(intent)


__all__ = [
    "SHOT_LIST_SYSTEM",
    "SYSTEM_PROMPT_TEMPLATE",  # re-export for convenience
    "build_shot_list_user_message",
    "assemble_offline_shot_list_prompt",
]
