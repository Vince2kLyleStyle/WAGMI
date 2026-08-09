# Autonomous Build Prompt

Copy everything between the `---` markers below into a Claude Code session at
`/home/user/WAGMI`. Claude will pick up where we left off and keep building
the memegine system.

Re-paste it any time (`/loop 15m` works too) — it's self-contained.

---

You are continuing work on `memegine/` — a local, zero-cost, offline-first
content-creation toolkit for @WillumpOnChain's X posting. Repo lives at
`/home/user/WAGMI`. Branch: `claude/memegine-telegram-pipeline-4otXQ`.

## Prime directive
Keep shipping. Don't stop to ask questions. Don't wait for confirmation.
If you hit a decision you genuinely cannot make, append a short note to
`memegine/MORNING-QUESTIONS.md` and pick the option most aligned with the
project's stated philosophy (free, offline-first, handmade-feel, no AI
slop) — then keep building.

## What's already shipped (don't rebuild these)
- Core brief engine: prompt_engine, shot_list, copy_writer, variants,
  reverse_engineer, pipeline
- Infrastructure: linter, archive (briefs JSONL), scorer (0-100 quality),
  capture (idea queue), telegram_bot (handler tests passing, awaits
  env vars to run live)
- Reference library: reference_lib, style_codex (living markdown doc)
- Video/image pipeline: editor (FFmpeg ops), image_ops (PIL), grading
  (19 presets), render_variants (aspect × grade matrix)
- Music-synced edits: audio (beat detection via librosa), transitions
  (xfade wrappers), music_edit (6 templates: hard_cut_montage,
  rhythmic_build, speed_ramp_slam, impact_frame_chain,
  aesthetic_slow_reveal, trailer_build), music_brief
- SFX: sfx (whoosh, impact, riser, click, layer_sfx)
- Formats: 26 in `data/formats/library.yaml`
- Playbooks (Claude reads on every brief): grok-imagine-patterns,
  video-img2vid-patterns, music-edit-patterns, meme-typography,
  x-content-playbook
- Claude Code slash commands: /piece /brief /shots /caption /reverse
  /batch /music-edit /codex-update
- Tests: 121 passing, end-to-end against real FFmpeg + librosa + PIL

## Rules you must follow
1. Every new module MUST have tests. No exceptions.
2. Run `cd memegine && python -m pytest tests/ -q` before each commit.
   If it's not green, don't commit — fix first.
3. Commit per logical unit with descriptive conventional-commit messages
   (feat: / fix: / refactor: / docs: / test:).
4. Push after each commit: `git push origin claude/memegine-telegram-pipeline-4otXQ`.
5. Never use the banned words (cinematic, epic, stunning, 4k, etc.) in
   prompts, captions, docstrings, or READMEs.
6. When adding a feature, wire it into the CLI (memegine <subcommand>)
   and mention it in README.
7. If a feature needs a playbook, write one in `data/playbooks/`.
8. Prefer depth over breadth — one well-tested feature > three half-built ones.

## Priority backlog (work top-down; reshuffle only if a blocker appears)

### Tier 1 — finish the music-edit vertical (the user's stated priority)

- **Text-on-beat**: new module `text_on_beat.py` + test. Given a video and
  a list of (text, beat_time, duration_beats) tuples, burn captions that
  appear exactly on their beats, using `image_ops._load_font` for font +
  `editor.drawtext` for compositing. Add `memegine music caption` CLI.
- **Per-segment grading inside music_edit templates**: extend
  `music_edit.hard_cut_montage` / etc. with an optional `grading_per_clip`
  list (preset name per clip or None for passthrough).
- **Audio-reactive grading**: subtle color shift on bass hits. Feature
  flag. Use librosa onset envelope + ffmpeg eq filter at bass-hit
  timestamps.

### Tier 2 — Telegram cockpit polish

- **Inline keyboards** for `/piece` replies: "Regenerate" / "Copy prompt"
  / "Send to queue" buttons. python-telegram-bot supports InlineKeyboard.
- **/render <draft_id>**: from a captured idea's brief, trigger the full
  pipeline rendering flow. Handler tests only; live-bot code path is
  behind a guard.
- **File-upload listener**: if the operator sends a photo to the bot,
  auto-save it to `data/references/inbox/` and run reverse-engineer
  analysis on it via a brief (do NOT try to actually analyze images
  client-side; just assemble the brief and send it back as a
  paste-to-Claude-Code message).

### Tier 3 — knowledge compounding

- **Brief analytics**: `memegine history stats` — 30-day breakdown of
  briefs by format, avg scorer score, top/bottom formats, capture
  consumption rate.
- **Codex auto-extract**: a script that reads recent archived briefs +
  the reference library and proposes codex updates (new winners, new
  flops, new proven patterns). Prints diff for human review; never
  auto-commits.
- **Style consistency checker**: given a batch of rendered files in a
  folder, probe their resolutions / aspect ratios / duration and flag
  outliers. Useful before posting a week's worth in one go.

### Tier 4 — more formats + playbooks (continuous)

Add formats when a real pattern emerges. Don't speculate. If you add
one, it needs: slug, kind, description, prompt_scaffold, good_models,
slot_hints if helpful. Update tests to assert the format loads.

Potential formats worth exploring if Tier 1-3 are done:
- `video_day_in_life` (character_3shot extended to video, 10-20s montage)
- `pixel_art_scene` (stylized pixel aesthetic for certain brands)
- `newspaper_front_page` (full-paper layout, multi-column)
- `album_cover` (square composition with artist/title typography)
- `magazine_spread` (2-column editorial with headline + body)

### Tier 5 — integration + hardening

- **End-to-end integration test**: brief → mock-Grok-output → render
  pipeline → archive lookup — a single test that exercises the whole
  surface.
- **CI setup**: add `.github/workflows/test.yml` that runs pytest on
  push. (Don't fail the push if there's no repo owner decision on CI;
  just create the file.)
- **Error message overhaul**: every RuntimeError in editor/grading/sfx
  should include a "suggested fix" line when possible.

## How to verify your work

After each feature:
```
cd /home/user/WAGMI/memegine
python -m pytest tests/ -q           # must be green
memegine <new_command> --help        # must show helpful help text
git log --oneline -5                 # confirm commit landed
git status                           # tree clean
```

## Where to put questions / uncertainties

If you genuinely can't decide something (e.g. "what should the default
BPM be for synthesized click tracks in CI smoke tests?") add it as a
single-line bullet in `memegine/MORNING-QUESTIONS.md` under the heading
`## Open questions` — THEN pick an option and ship.

## End-of-turn summary

Every time this prompt is re-entered and you stop, write ONE sentence to
`memegine/SESSION-LOG.md` under today's date: what you shipped, tests
added, commits pushed. Keep it to under 30 words.

## Go
Continue from the current state of the repo. Start with Tier 1. Ship.
