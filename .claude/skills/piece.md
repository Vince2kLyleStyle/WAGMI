# /piece — End-to-end piece brief (the main command)

## Description
One command per piece. Bundles:
1. The image/video brief (directed prompt)
2. The caption brief
3. For video: the shot list too

Saves everything to a dated folder under `memegine/data/outputs/`. Output: the
operator opens the folder, executes Grok with the prompts, and posts.

Use this instead of `/brief` + `/caption` when you want one deliverable end
to end.

## Arguments
- `$ARGUMENTS` — Operator intent + optional flags:
  - `--kind image|video` (defaults to image)
  - `--format <slug>` (required for kind=image; ignored for video)

Examples:
- `/piece trader at 3am market dumps`
- `/piece slow push-in on a trader's face --kind video`
- `/piece kilroy watching the fed --format meme_two_panel`

## Workflow

### 1. Parse
- Read the intent (everything up to the first `--`).
- Parse `--kind`. Default to `image` if absent.
- Parse `--format`. If `--kind image` and no format given, pick best-fit
  format from `data/formats/library.yaml` yourself and state your pick.

### 2. Run the pipeline command
```
cd memegine && memegine pipeline "<intent>" --kind <kind> [--format <slug>]
```

Note the folder path it prints. Open each `.md` file inside.

### 3. For each brief in the folder:
- Read the SYSTEM + USER blocks.
- Act as the role described in the SYSTEM block (Director or Copywriter).
- Output the JSON response directly in this session.
- Self-lint every production prompt (`memegine lint`).

### 4. Present the full bundle to the operator
- Folder path for reference
- For kind=image: the directed prompt (paste into Grok) + 3 captions
- For kind=video: the shot list + per-shot prompts + 3 captions
- Next-step reminder: the FFmpeg edit commands for video, or the refs/codex
  commands for image

### 5. Update tracking
After presenting, remind the operator:
- "When a piece lands: `memegine refs add <file> --tags <tags> --notes
  <why>` and `memegine codex winner '<prompt>' 'why'`"
- "The briefs are archived at `memegine/data/logs/briefs-<today>.jsonl`
  and the bundle folder for full reference."

## Notes
- This is the highest-leverage command. Use it for anything meant to be a
  real post.
- If the piece fails in Grok, rerun the brief alone via `/brief` with tweaks
  rather than restarting the whole pipeline.
