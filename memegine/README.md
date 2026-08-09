# Memegine — Director's Assistant for Elite Photo/Video Content

A zero-cost, offline-first toolkit that turns rough operator intent into
production-grade prompts you paste into **Grok** (Nano Banana / Aurora /
Ideogram / Grok Imagine video). You supply the taste and the edits. Memegine
handles the briefs, the style memory, the reference library, the copywriting,
and the FFmpeg-based editing so you never open CapCut.

**Stack: free. Claude Code + Grok (via X Premium). Nothing else.**

---

## The iron triangle

| Free? | Elite? | Push-button? |
|---|---|---|
| ✅ | ✅ | ❌ |

You can have two of three. Memegine picks free + elite. Push-button means
paid APIs ($100-300/mo), and we don't need them. Your hands stay on the
wheel; memegine removes everything else.

---

## What's inside

### The workflow

```
rough intent
    │
    ▼
 /piece <intent>          ← Claude Code slash command (or `memegine pipeline`)
    │
    ▼
Director assembles brief  ← reads style codex + craft playbooks + format
    │
    ▼
You paste prompt into Grok (Nano Banana / Aurora / Ideogram / Grok Imagine)
    │
    ▼
You iterate in Grok until it lands
    │
    ▼
memegine refs add <file>   (your best outputs grow the reference library)
memegine codex winner      (patterns compound into future briefs)
    │
    ▼
memegine edit ...          (FFmpeg-based stitching/aspect/grade/caption — no CapCut)
    │
    ▼
Post on X
```

### The modules

- **`prompt_engine`** — assembles a SYSTEM + USER brief from intent + format
  + style codex + craft playbooks
- **`shot_list`** — shot-list brief for short (3-12s) video pieces
- **`copy_writer`** — X caption brief that doesn't sound AI
- **`variants`** — variant generator (one winning prompt → N axis-varied
  versions)
- **`reverse_engineer`** — image → recreate-the-look prompt (uses Claude's
  vision)
- **`linter`** — catches banned words (cinematic, epic, 4k...) and warns
  when a prompt lacks named lens/film/lighting/time/composition
- **`archive`** — every brief saved to `data/logs/briefs-YYYY-MM-DD.jsonl`;
  `memegine history` surfaces them
- **`pipeline`** — one command, one folder, every brief for a whole piece
- **`reference_lib`** — local tagged library of your winners, indexed JSON
- **`style_codex`** — living markdown doc of winners + kill list + visual DNA
- **`editor`** — FFmpeg wrappers: probe, to_aspect, ken_burns, concat,
  crossfade, drawtext, add_audio, speed
- **`image_ops`** — PIL wrappers: to_aspect, caption (auto-sized Impact
  style), grid, two_panel, blur_background_portrait
- **`grading`** — 10 filmic color presets via FFmpeg (cinestill_800t,
  portra_400, tri_x_bw, teal_orange, moody_film, kodachrome, golden_hour,
  neon_night, faded_print, bleach_bypass)

### The data

- **`data/formats/library.yaml`** — 14 format templates (photoreal_portrait,
  meme_two_panel, drake_yes_no, photoreal_scene_motion, lore_drop,
  cope_chart, npc_wojak_row, split_screen_then_now, photoreal_product_shot,
  photoreal_street_scene, reaction_shot_meme, fake_news_headline,
  video_single_take_reaction, video_kenburns_still)
- **`data/playbooks/`** — four craft bibles Claude reads on every brief:
  - `grok-imagine-patterns.md` — prompt craft for Nano Banana / Aurora /
    Ideogram / Flux via Grok
  - `video-img2vid-patterns.md` — img2vid and shot recipes
  - `meme-typography.md` — text-on-image rules
  - `x-content-playbook.md` — caption patterns, cadence, what works on X
- **`data/codex/style.md`** — your living style memory (empty skeleton to
  start; grows every session)
- **`data/references/`** — your tagged reference library (local, .gitignored)
- **`data/outputs/`** — pipeline bundle folders per piece (.gitignored)
- **`data/logs/`** — archived briefs JSONL per day (.gitignored)

---

## Install

```bash
cd memegine
pip install -e .          # core, offline-only
# optional:
# pip install -e .[online]  # adds Anthropic SDK for programmatic use later
# pip install -e .[dev]     # adds pytest
```

**Requires**: Python 3.10+, FFmpeg on PATH (for the editor module).

---

## Commands

### Brief generation
```bash
memegine formats                                   # list 14 formats
memegine prompt "<intent>" -f <format>             # one brief, printed
memegine shots "<intent>"                          # shot-list brief for video
memegine copy "<concept>" --kind image|video      # X caption brief
memegine pipeline "<intent>" --kind image -f <fmt> # bundle (brief + copy)
memegine pipeline "<intent>" --kind video          # bundle (shots + copy)
memegine variants "<winning prompt>" -n 6          # variant brief
memegine reverse <image.png> --context "..."       # reverse-engineer a look
memegine lint "<prompt>"                           # catch banned words
memegine lint "<prompt>" --motion                  # lint motion prompts
```

### Style memory
```bash
memegine codex show
memegine codex winner "<prompt>" "why it worked"
memegine codex flop "<what>" "why"
```

### Reference library
```bash
memegine refs add <image> --tags "night,neon" --source grok --prompt "..." --notes "the one"
memegine refs search --tags "night,neon"
memegine refs recent -n 20
```

### Brief archive
```bash
memegine history recent -n 20
memegine history show <id>
memegine history search "<text>"
```

### Music-synced edits
```bash
memegine music beats <audio.mp3>                              # BPM + beats + estimated drop
memegine music plan <audio.mp3> "<intent>" <clip1> <clip2>    # ask Claude to plan the edit
memegine music hardcut out.mp4 music.mp3 a.mp4 b.mp4 c.mp4    # N clips, 1 cut per beat
memegine music build out.mp4 music.mp3 clips...               # accelerating cuts into a drop
memegine music slam out.mp4 music.mp3 clip.mp4 --slam 3.0     # slow-mo into a beat, snap
memegine music impact out.mp4 music.mp3 clips...              # flashes between cuts
memegine music reveal out.mp4 music.mp3 still.png --duration 8 # slow push on a still
memegine music trailer out.mp4 music.mp3 clips... --slam 6.0  # build + slam + held hero
memegine music transitions                                     # list transition presets
memegine music transition out.mp4 a.mp4 b.mp4 --preset flash_white
```

### Editor (the "no CapCut" layer)
```bash
memegine edit aspect <src> <dst> --ratio 9:16 --fit cover
memegine edit kenburns <image.png> <out.mp4> -d 5 -r 9:16
memegine edit concat <out.mp4> <clip1.mp4> <clip2.mp4> <clip3.mp4>
memegine edit caption <src> <dst> "HELLO WORLD" --pos bottom
memegine edit grade <src> <dst> --preset cinestill_800t
memegine edit presets
memegine edit speed <src> <dst> 2.0
memegine edit audio <src> <audio> <dst> --mode mix --volume 0.8
memegine edit grid <dst> <img1> <img2> <img3> <img4> --cols 2
memegine edit two-panel <top> <bottom> <dst>
```

---

## Claude Code slash commands

If you're using memegine through a Claude Code session (recommended), these
slash commands are faster than the CLI:

- **`/piece <intent> [--kind image|video] [--format <slug>]`** — the main
  command. One-shot from intent to a ready-to-post bundle.
- **`/brief <intent> [-f <format>]`** — just the image brief, Claude returns
  JSON directly in the session
- **`/shots <intent>`** — shot list for video
- **`/caption <concept>`** — X captions
- **`/reverse <image_path> [context]`** — reverse-engineer a look
- **`/batch <n> <theme>`** — batch produce N briefs across varied formats
- **`/codex-update <learnings>`** — update style memory after a session

---

## The rhythm (what using this actually looks like)

### Morning session (10 min)
1. `/piece reactive meme about the overnight news`
2. Paste the prompt into Grok, iterate 3-5 times, pick the winner
3. `memegine refs add <file> --tags reaction,news --notes "landed because..."`
4. Post caption from the brief

### Afternoon hero piece (30-45 min)
1. `/piece photoreal trader at 3am market dumping --kind image -f photoreal_portrait`
2. Execute in Grok. Take multiple generations. If text comes back garbled,
   regenerate image without text and `memegine edit caption` locally.
3. Refine aspect: `memegine edit aspect out.png final.png --ratio 9:16`
4. Grade: `memegine edit grade final.png graded.png --preset cinestill_800t`
5. `refs add` + `codex winner` to compound
6. Post

### Evening video piece (45-60 min)
1. `/shots slow push on a trader, market dumping, 5 seconds`
2. Generate hero still for each shot in Grok (still_prompt from the brief)
3. Animate each still via img2vid in Grok (motion_prompt from the brief)
4. `memegine edit concat out.mp4 shot1.mp4 shot2.mp4 shot3.mp4`
5. `memegine edit grade out.mp4 graded.mp4 --preset moody_film`
6. `memegine edit caption graded.mp4 final.mp4 "THE END" --pos bottom`
7. `memegine edit audio final.mp4 music.mp3 posted.mp4 --mode replace`
8. Post

### End of day (5 min)
1. `/codex-update <session notes>` — log what worked, what flopped
2. Your style memory is sharper for tomorrow

---

## Philosophy

- **Every piece is handmade.** The tool removes friction, never replaces
  judgment.
- **Style compounds.** Week 10 is sharper than week 1 because the codex +
  reference library + winning patterns all accumulate.
- **Never ships AI-slop words.** The linter fails prompts containing
  cinematic, epic, stunning, 4k, etc.
- **Names the craft.** Lenses, film stocks, lighting setups, camera moves —
  by name, not by adjective.
- **Captions that don't sound AI.** No emojis, no hashtags, no "gm/wagmi/
  lfg", no engagement-bait.
- **Offline-first.** No external API calls unless you opt in. Everything
  runs locally on disk.

---

## Testing

```bash
cd memegine && python -m pytest tests/ -v
```

Current: 61 tests across 6 files. Editor tests use real FFmpeg + PIL (no
mocks), so they verify actual output. Skip editor/grading tests if FFmpeg
isn't installed (they auto-skip via pytest marker).

---

## Next steps (when you're ready)

- **Seed the codex** — fill `data/codex/style.md` with real voice notes.
  Your first 10-20 refs tagged and noted is the biggest unlock.
- **Extend formats** — add a new format when you find a pattern Grok nails
  reliably for you.
- **Train your eye** — save 20-40 reference images from top creators you
  admire. Run `/reverse` on them. Internalize the patterns.
- **V2 ideas**: Telegram-bot delivery of briefs, Discord integration for
  ref library, scheduled brief batches, auto-export to posting templates.

---

## Directory layout (reference)

```
memegine/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── src/memegine/
│   ├── __init__.py
│   ├── cli.py                  # Typer entrypoint (memegine <cmd>)
│   ├── config.py               # pydantic settings
│   ├── prompt_engine.py        # brief assembly + playbooks + codex
│   ├── shot_list.py            # video shot-list brief
│   ├── copy_writer.py          # X caption brief
│   ├── variants.py             # variant brief
│   ├── reverse_engineer.py     # image → look prompt
│   ├── pipeline.py             # bundle (brief + shots + copy per piece)
│   ├── linter.py               # ban words + craft coverage
│   ├── archive.py              # briefs JSONL archive
│   ├── reference_lib.py        # local tagged library
│   ├── style_codex.py          # living style doc read/write
│   ├── editor.py               # FFmpeg ops (video + still)
│   ├── image_ops.py            # PIL ops (aspect/caption/grid/panel)
│   ├── grading.py              # 10 filmic color presets
│   └── claude_client.py        # optional Anthropic SDK wrapper
├── data/
│   ├── codex/style.md          # living style memory
│   ├── formats/library.yaml    # 14 format templates
│   ├── playbooks/              # 4 craft bibles
│   ├── references/             # your refs (gitignored)
│   ├── outputs/                # pipeline bundles (gitignored)
│   └── logs/                   # archived briefs (gitignored)
└── tests/
    └── test_*.py               # 61 passing tests
```
