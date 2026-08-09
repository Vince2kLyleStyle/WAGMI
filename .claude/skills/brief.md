# /brief — Generate a Grok-ready prompt brief

## Description
Produce a production-grade image prompt brief for the operator to paste into
Grok (Nano Banana / Aurora / Ideogram). Reads the project style codex and
craft playbooks to ensure the brief follows the rules (no banned words,
named lens/film-stock/lighting/time/composition).

## Arguments
- `$ARGUMENTS` — Rough operator intent. Optional trailing `-f <format_slug>`
  to lock the format. If omitted, choose the best-fitting format from the
  library yourself.

## Workflow

### 1. Parse arguments
- The intent is free text up to the first `-f` flag (if present).
- If `-f <slug>` is present, use that format slug.
- If not, read `memegine/data/formats/library.yaml` and pick the format that
  best fits the intent. Justify your pick in one sentence at the top of the
  output.

### 2. Assemble the brief
Run:
```
cd memegine && memegine prompt "<intent>" -f <format_slug>
```

This prints a SYSTEM + USER block. The USER block now includes all relevant
craft playbooks (grok-imagine-patterns, video-img2vid-patterns if video,
meme-typography) and the current style codex.

### 3. Produce the JSON brief yourself
Act as the Director described in the SYSTEM prompt. Read the USER block and
output the required JSON directly in this session — the operator shouldn't
have to paste into a second Claude.

The JSON MUST include:
- `format_slug`
- `model_route` (pick from the format's eligible models)
- `prompt` (the production prompt, ready for Grok)
- `negative_prompt`
- `variants_to_try` (3 one-line axis tweaks)
- `rationale`
- `post_caption_ideas` (3 captions, different lengths/registers)
- `next_move_if_this_lands`

### 4. Self-lint
Before returning the JSON, run:
```
cd memegine && memegine lint "<your prompt>"
```
If it fails, fix the prompt and re-lint before presenting. Never ship a brief
that contains banned words.

### 5. Present
Print:
- The chosen format + one-sentence rationale
- The JSON brief (the operator copies `prompt` into Grok)
- A reminder: *"After it lands, run `memegine refs add <file> --tags ...` to
  grow your library, and `memegine codex winner '<prompt>' 'why'` to
  compound the style memory."*

## Notes
- Never generate images yourself — you're writing the brief for Grok.
- If the intent is ambiguous, pick the most-likely format and produce ONE
  brief. Ask clarifying questions only if the intent is fundamentally
  unclear.
- The brief is archived automatically to `memegine/data/logs/briefs-*.jsonl`.
