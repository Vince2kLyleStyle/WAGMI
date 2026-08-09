# /music-edit — Plan and render a beat-synced video

## Description
Given a music file and a set of clips (or clip descriptions), plan a full
music-synced edit and output the exact `memegine music` command to render
it.

## Arguments
- `$ARGUMENTS` — Free-text intent + music path + clip paths. The intent
  comes first; the tool will parse out paths.

Example:
```
/music-edit aggressive build to the drop, money shot at 7s
  music.mp3 clip1.mp4 clip2.mp4 clip3.mp4 hero.mp4
```

## Workflow

### 1. Parse arguments
Extract:
- The intent (free text)
- The music file path (first existing audio file)
- The clip paths (in order)

If unclear, ask the operator to restate with clear positional args.

### 2. Analyze the music
```
cd memegine && memegine music beats <music_path>
```
Note the tempo, duration, total beats, and estimated drop time.

### 3. Assemble the planning brief
```
cd memegine && memegine music plan <music_path> "<intent>" <clip1> <clip2> ...
```

This prints a SYSTEM + USER block with the full music metadata + clip list
+ style codex + music-edit playbook.

### 4. Produce the JSON plan yourself
Act as the Editor per the SYSTEM prompt. Output the JSON:
- `template` — pick the best-fitting template (hard_cut_montage,
  rhythmic_build, speed_ramp_slam, impact_frame_chain,
  aesthetic_slow_reveal, trailer_build, or custom)
- `total_duration_sec`
- `aspect_ratio` (default 9:16)
- `music` with start_sec, end_sec, slam_beat_sec
- `clip_order` — 0-indexed list of how clips appear
- `segments` — per-segment breakdown with transitions, duration, text overlay
- `cli_command` — the exact `memegine music <subcommand> ...` to run
- `rationale` — why this template, why this pacing
- `x_caption_ideas` — 3 X caption options
- `grading_suggestion` — one of 10 grading presets or "none"

### 5. Self-check the plan
- Total duration must be ≤ 30s (X autoplay window)
- At least 4 cuts for any montage
- No banned words in any text overlay
- Text overlays: max 3 per edit, 1-3 beats each, all-caps single words

### 6. Present
Print:
- The plan summary (template, duration, segment count)
- The exact CLI command (highlighted — this is what the operator runs)
- The suggested grading command (`memegine edit grade ...`)
- The 3 X caption ideas
- A reminder: "After it lands: `memegine codex winner '<plan>' '<why>'` to
  save the template for reuse."

## Notes
- If the operator's clips don't fit the music vibe, say so in rationale
  and suggest which template/pacing would work better with different clips.
- The music-edit-patterns.md playbook is authoritative — defer to it over
  your training intuitions.
- The `drop_sec` from beat analysis is an *estimate*. If the plan calls for
  a slam and the auto-detect seems wrong, suggest the operator eyeball the
  music and pass `--slam <seconds>` explicitly.
