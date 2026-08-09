# /shots — Plan a short video as a shot list

## Description
Produce a shot-list brief for a short (3-12s) video piece destined for
X/Twitter. Enforces the video craft rules: max 3 shots, named camera moves
only, explicit per-shot still + motion prompts.

## Arguments
- `$ARGUMENTS` — Rough operator intent for the video (e.g. "slow push on a
  trader's face as the market dumps, 5 seconds").

## Workflow

### 1. Assemble the brief
```
cd memegine && memegine shots "<intent>"
```

This prints the SYSTEM + USER block with the shot-list rules and the
style codex and video playbook.

### 2. Produce the JSON yourself
Act as the Director. Output the JSON described in the SYSTEM prompt:
- `total_duration_sec`
- `aspect_ratio` (default 9:16)
- `shots[]` with index, scene, camera_move, duration_sec, lens_and_film,
  lighting, still_prompt, motion_prompt
- `cuts[]` (hard cut / match cut / etc.)
- `audio.has_audio` + `kind` + `cue`
- `x_caption_ideas[]` (3 options)
- `justification` (one sentence)

### 3. Self-lint every still_prompt and motion_prompt
For each shot, run:
```
cd memegine && memegine lint "<still_prompt>"
cd memegine && memegine lint "<motion_prompt>" --motion
```
If any lint fails, revise the prompt before presenting.

### 4. Present
Print the full shot list with per-shot briefs. Close with the production
workflow:
- "For each shot: generate the still in Grok, iterate to a winner, then
  animate with img2vid using the motion_prompt."
- "Once you have all shots: `memegine edit concat <shot1> <shot2> ... <out>`"
- "Grade with `memegine edit grade --preset <preset>` and burn caption
  with `memegine edit caption` if needed."

## Notes
- Shots max: 3. Two is usually better. One is often best.
- Never use "cinematic" in any prompt.
- Per-shot duration between 2-6 seconds.
- Default aspect ratio 9:16 for X unless operator specified otherwise.
