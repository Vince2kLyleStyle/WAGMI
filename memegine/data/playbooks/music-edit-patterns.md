# Music-Edit Patterns

Craft bible for short (5-30 sec) beat-synced edits destined for X/Twitter.
Read before every music-edit brief.

The core insight: **cut on the beat**, not near it. One frame early or late
and the whole edit feels off. Memegine's `music_edit` module enforces this
automatically via beat detection + snapping.

---

## The six templates

### 1. hard_cut_montage
**What**: N clips, one cut per beat (or per 2/4 beats). Hard cuts only.
Music carried through.
**When**: Building energy, visual variety, hip-hop / trap / house. The
default music-edit move.
**Length**: 4-16 clips, 4-20 sec total.
**CLI**: `memegine music hardcut out.mp4 music.mp3 a.mp4 b.mp4 c.mp4 ...`

**Rules**:
- 4+ cuts minimum. 3 cuts reads as "nothing happened".
- Clips should have visual variety between cuts — same subject across 8
  cuts = visual tinnitus.
- `--bpc 2` (cut every 2 beats) is often better than every beat for pacing.
  Every-beat cuts land best at fast BPMs (140+) or for short (<6s) edits.

### 2. rhythmic_build
**What**: Cuts start long (4-beat holds) and accelerate to 1-beat cuts.
**When**: Building into a drop, narrative ramp-up, trailer openers.
**Length**: 8-20 sec.
**CLI**: `memegine music build out.mp4 music.mp3 clips... --start-per-cut 4 --end-per-cut 1`

**Rules**:
- Pairs naturally with `trailer_build` for the full arc.
- The first 2-3 long holds should be your *most beautiful* clips — people
  stay or scroll based on these.
- The acceleration should peak right at a strong moment (chorus, drop,
  section change).

### 3. speed_ramp_slam
**What**: Slow-motion leading into a named beat, then snap to normal speed
on the beat.
**When**: The money moment. A single hero shot with tension. The "reveal".
**Length**: 3-6 sec.
**CLI**: `memegine music slam out.mp4 music.mp3 clip.mp4 --slam 3.0 --ramp-in 1.5 --slow 0.4 --post 1.0`

**Rules**:
- The slow-mo section should be 0.3-0.6x real speed. Slower than 0.3x
  looks like lag.
- Post-slam hold should be 1-2 seconds — just enough to land, not enough
  to become boring.
- Best with clips that have one clear action moment (e.g. a turn, a look,
  a gesture) where the slow-mo stretches the anticipation and the slam
  delivers the payoff.

### 4. impact_frame_chain
**What**: Hard cuts with 2-frame white/black flashes between clips on beats.
**When**: Aggressive, maximalist edits. Hype pieces. Workout/action.
**Length**: 4-15 sec.
**CLI**: `memegine music impact out.mp4 music.mp3 clips... --flash white --frames 2 --bpc 1`

**Rules**:
- 2-3 frames of flash is the sweet spot. 1 frame = barely visible. 5+ =
  annoying.
- White flash = energetic, sunny; black flash = moody, ominous.
- Don't chain more than 8 flashes in a row — seizure territory + fatigue.

### 5. aesthetic_slow_reveal
**What**: One clip or still, slow push-in, music underneath.
**When**: Mood pieces, single hero shots, cinematic singles. The anti-edit.
**Length**: 6-12 sec.
**CLI**: `memegine music reveal out.mp4 music.mp3 still.png --duration 8`

**Rules**:
- Pair with ambient/atmospheric music, not percussive.
- The still/clip must be strong on its own — no edit can rescue a weak
  image held for 8 seconds.
- Default zoom is 1.0 -> 1.08 (8% push). Going beyond 1.15 feels like the
  image was cropped wrong.

### 6. trailer_build
**What**: Long cuts -> accelerating -> slam on the drop -> held hero shot.
**When**: Movie-trailer-style pieces with a clear structural arc. Product
reveals. "Big" announcements.
**Length**: 12-25 sec.
**CLI**: `memegine music trailer out.mp4 music.mp3 clips... --slam 6.0 --pre-build 6 --post-slam 2`

**Rules**:
- Needs 3+ clips. Last clip is the hero (held through and after the slam).
- `--slam` at 50-70% through the piece is the sweet spot.
- Works best with music that has a clear build-into-drop structure.
  Failing that, any track with a strong single impact at a known time.

---

## Picking the template from the music

| Music vibe | Default template |
|---|---|
| Trap / hip-hop with heavy kicks | hard_cut_montage, bpc=2 |
| Phonk / drift vibes | impact_frame_chain (black flash) + hard_cut_montage |
| Lo-fi / ambient / cozy | aesthetic_slow_reveal |
| Build + drop (EDM, hyperpop, dubstep) | trailer_build or rhythmic_build + slam |
| Orchestral / epic / movie score | trailer_build |
| Punk / punk-adjacent / fast rock | impact_frame_chain (white flash), bpc=1 |
| Chill R&B / slow | aesthetic_slow_reveal or hard_cut_montage bpc=4 |

---

## Cuts on the beat, not through it

FFmpeg respects frame boundaries. At 30fps, each frame = 33ms. A beat at
0.5s lands at frame 15. A cut at 0.49s or 0.51s — the viewer can feel it
even if they can't articulate why.

`music_edit` uses beat timestamps straight from librosa's onset detector,
which is accurate to ~10ms. Your cuts ARE on the beat if you let the tool
place them. Don't try to manually offset — you'll only drift.

---

## BPM and cut rhythm cheatsheet

| BPM | Per-beat time | Suggested bpc for montage | Why |
|---|---|---|---|
| 60 | 1.000s | 1 | Each beat holds a full clip comfortably |
| 80 | 0.750s | 1 | Still slow enough to read |
| 100 | 0.600s | 1-2 | Edge case — 2 for breathing room |
| 120 | 0.500s | 2 | Every-beat cuts feel frantic |
| 140 | 0.429s | 2 | Every-beat = unreadable |
| 160 | 0.375s | 2-4 | Consider half-time feel |
| 180+ | 0.333s | 4 | Cut on downbeats only |

Rule: if a single clip can't be recognized in one cut, cut less often.

---

## Text overlays on beats

When to add text:
- Label moments ("DAY 1" / "DAY 14" / "DAY 30")
- Amplify a slam ("HERE" / "NOW" / "GO")
- Count-in before a build ("3" / "2" / "1")
- Punchline at the end of an edit (single word)

When NOT to add text:
- During the slow reveal (let the image breathe)
- When the music is already doing the talking
- More than 3 text overlays in a single edit (unless it's a counting
  pattern)

Rules for text that hits on the beat:
- Appears ON a beat, not between
- Lives for 1-3 beats, then disappears
- Single word or 2-3 word max per overlay
- Impact Bold, white with black stroke, dead-center OR bottom-third
- NO emojis, NO emoji-adjacent characters, NO punctuation

---

## Clip selection for music edits

Your Grok outputs ARE your clip library. Every time a clip lands, add it
to the reference library with tags that will help you find it for the next
edit:

```
memegine refs add sky-timelapse.mp4 --tags "clip,sky,wide,cool-tones,motion" \
  --notes "slow clouds moving right, pairs with 120 BPM builds"
```

Tag suggestions:
- **Subject**: person, place, object, abstract
- **Motion**: static, slow-motion, fast, push-in, pull-out
- **Color mood**: warm, cool, monochrome, high-contrast, pastel
- **Vibe**: energetic, somber, hype, contemplative
- **Tempo fit**: slow-BPM, medium-BPM, fast-BPM

When planning a new edit: `memegine refs search --tags "wide,cool-tones"` — the library suggests itself.

---

## Grading the edit

After rendering, apply a grade for unity across heterogeneous clips:

- `cinestill_800t` — moody night edits, neon context
- `portra_400` — warm, human-feeling edits
- `tri_x_bw` — high-contrast action, "noir"
- `teal_orange` — blockbuster-movie edits, product reveals
- `moody_film` — introspective, slow reveals
- `bleach_bypass` — aggressive action / impact_frame_chain pieces

```
memegine edit grade out.mp4 final.mp4 --preset teal_orange
```

A consistent grade hides that your clips came from 5 different Grok
generations. This is the single biggest quality unlock for multi-clip edits.

---

## Music sourcing (free, legal-ish)

- **Your own tracks** if you produce
- **Suno free tier** — AI-generated, copyright is murky for commercial use
  but fine for personal/creator use as of 2026
- **Udio free tier** — same as Suno
- **YouTube Audio Library** — royalty-free, organized by mood/genre
- **Pixabay Music** — CC0, decent selection
- **Freesound.org** — SFX, ambient beds, not full tracks

Don't use copyrighted music on X. It won't get muted like YouTube, but it
can get your post removed and/or hurt your reach.

---

## The "save a template" workflow

When a specific edit pattern lands (say, a trailer_build with specific
pre-build/post-slam numbers + a certain grading preset), save it to the
codex:

```
memegine codex winner "trailer_build --pre-build 6 --post-slam 2 --slam <drop> | teal_orange grade | 9:16 | 18s" \
  "worked for product reveal with EDM track; hero shot held through slam + 2s read"
```

Next time you do a similar piece, reuse the settings exactly. The codex
accretes personal templates over time.

---

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Feels "off" to the beat | Clips trimmed non-frame-accurately | Use `memegine music hardcut` — it handles frame snapping |
| Cuts feel too frantic | BPM too fast for bpc=1 | Raise bpc to 2 or 4 |
| Build feels flat | `start_per_cut` too low | Start with 4-8 beat holds, accelerate |
| Slam doesn't land | Wrong slam time | Use `memegine music beats` to confirm drop time; auto-detect may be off by 1-2 beats |
| Flashes look seizure-y | Too many in a row | Cap at 6-8 flashes per edit, break with a hold |
| Looks like 3 different edits | Clips too disparate visually | Apply a unifying grade, or regenerate clips with a consistent palette |
| Music cuts off abruptly | Final duration math | Use `--post-slam` generously (2+ sec); the music needs room to breathe out |
| Text overlays miss the beat | FFmpeg drawtext with bad timing | Use `memegine edit caption` on the final output; simpler than in-edit timing |

---

## Workflow checklist for a music edit

1. Pick music track.
2. `memegine music beats <track>` — confirm BPM, duration, drop.
3. Gather/generate 4-12 clips via Grok (or pull from refs library).
4. `memegine music plan <track> "<intent>" <clip1> <clip2> ...` — Claude
   outputs the edit plan + exact CLI command.
5. Execute the CLI command → silent render with music.
6. Grade: `memegine edit grade <render> <graded> --preset <choice>`.
7. Caption if needed: `memegine edit caption <graded> <captioned> "TEXT" --pos bottom`.
8. Aspect-confirm: default is 9:16. `memegine edit aspect` if you need
   1:1 or 16:9.
9. Post. Add `refs add` + `codex winner` for the render if it lands.
