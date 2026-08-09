# /batch — Produce N briefs across formats in one go

## Description
When the operator wants volume (e.g. "10 posts for tomorrow"), generate a
varied batch of briefs across multiple formats. Output is a single folder
with N numbered subfolders, each a complete bundle.

## Arguments
- `$ARGUMENTS` — `<n> <theme>` where:
  - `n` is the count (default 10)
  - `theme` is the shared intent / vibe (e.g. "today's Fed announcement" or
    "end-of-year recap vibes")

Example: `/batch 10 the fed just pivoted`

## Workflow

### 1. Plan the mix
Given `n`, pick a format mix that balances reach + saves + replies:
- 30% reactive (reaction_shot_meme, meme_two_panel)
- 20% photoreal (photoreal_portrait or photoreal_street_scene)
- 20% short video (video_single_take_reaction or photoreal_scene_motion)
- 15% text-heavy (fake_news_headline, cope_chart)
- 15% lore / wildcard (lore_drop, npc_wojak_row, split_screen_then_now)

Example for n=10:
- 3 reactive memes
- 2 photoreal stills
- 2 short videos
- 2 text-heavy screenshots
- 1 lore drop

### 2. Generate per-brief intents
For each slot, write a specific intent that applies the `theme` to that
format. Be concrete — "Fed pivot + kilroy omnipresence" not "post about the
fed".

### 3. Run /piece N times
For each intent, produce the full piece brief (same shape as `/piece`).
Number them 01 / 02 / ... / N.

Collect all briefs into a single parent folder under
`memegine/data/outputs/YYYY-MM-DD_batch_<theme-slug>/`.

### 4. Present the batch
- One-page overview: a table of `# | format | intent | caption_teaser`
- Then each numbered brief in order
- Close with the execution checklist: "Open each in order, execute in Grok,
  post across the day using the cadence in `x-content-playbook.md`."

### 5. Warn if the batch is monotonous
If more than 2 consecutive briefs use the same format, flag it. Vary.

## Notes
- The batch is saved so the operator can work through it over multiple
  sessions. Each brief folder is self-contained.
- The style codex reads once for the whole batch — all briefs inherit the
  same voice. That's the compounding effect you want.
- Recommended target: 5-15 briefs per batch. Beyond that, diminishing
  returns and taste starts slipping.
