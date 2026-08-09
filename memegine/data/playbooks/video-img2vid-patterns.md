# Video + img2vid Prompt Patterns

How to prompt Grok Imagine's video mode (and equivalents: Kling 2.1 Master,
Veo 3, Runway Gen-4, Luma Ray 2) for short (3-10s) clips that don't morph,
don't drift, and don't look like AI slop. Read before every video brief.

## Rule zero — still first, always

The hero still is 80% of the final piece. A bad still img2vid'd becomes a bad
video with motion. **Never generate a video from a text-only prompt if you can
generate a great still first and animate it.**

Workflow:
1. Generate hero still via photoreal still pattern (see `grok-imagine-patterns.md`).
2. Iterate the still until it's a single-frame piece you'd post as a photo.
3. Animate the still with img2vid using the motion prompt template below.
4. If multiple shots: generate each still, animate each, stitch in FFmpeg.

---

## The camera move taxonomy (name one and only one)

Video models fight you when you ask for "cinematic camera movement". They
obey when you name a single move from this list:

| Move | When to use | Prompt phrase |
|---|---|---|
| **Static lockoff** | Performance / reaction / dialogue. The face IS the shot. | "static lockoff, no camera movement, subject motion only" |
| **Slow push-in** | Building tension, "money shot" on a hero | "slow push-in, camera moves forward 15cm over the duration" |
| **Slow pull-out** | Reveal, context, loneliness | "slow pull-out, camera recedes 20cm, revealing the wider scene" |
| **Rack focus** | Directing attention between two planes | "rack focus from foreground object to subject's eyes over 1.5s" |
| **Orbit** | Product shots, hero objects | "slow orbit, 15 degrees of travel around the subject" |
| **Ken Burns** | Turning a still into motion without real camera work | "Ken Burns zoom and pan, start wide, end tight on subject's face" |
| **Whip pan** | Transition in / out to next clip | "whip pan left-to-right, motion blur on the whip" |
| **Tilt up / down** | Reveal from ground to sky (or reverse) | "slow tilt up, from hands to face, over the duration" |
| **Dolly side** | Tracking with a moving subject | "dolly side, camera tracks the subject walking left to right at walking pace" |
| **Handheld micro-shake** | Documentary feel, realism | "handheld, subtle micro-shake, as if shot on a shoulder rig" |

**Never**: "dynamic camera", "dramatic movement", "sweeping", "flowing",
"cinematic". All meaningless, all produce morphing.

---

## Motion prompt template (img2vid)

```
Camera move: <ONE named move from the list above>.
Duration: <3-10 seconds>.
Subject motion: <what the subject does — micro-expressions ONLY unless a
full body action is required>.
Lighting: stays consistent, no flicker, no relight.
Style: matches the still exactly, no style drift, no wardrobe changes,
no face morph.
No cuts within this clip.
```

### Example — trader reaction
```
Camera move: static lockoff, no camera movement.
Duration: 5 seconds.
Subject motion: the trader's expression slowly drops — eyes widen
slightly over 2s, jaw tightens at 3s, exhales at 4s. No body movement
beyond micro-expressions. Eyes stay on the phone screen throughout.
Lighting: consistent, desk lamp stays at the same intensity.
Style: matches the still exactly, no wardrobe changes, no face morph,
no room dimension changes.
No cuts.
```

### Example — street push-in
```
Camera move: slow push-in, camera moves forward 20cm over the duration.
Duration: 6 seconds.
Subject motion: man keeps lighting the cigarette, one continuous action —
lifts lighter at 1s, flame catches at 2.5s, first inhale at 4s, exhale
begins at 5s. Rain continues falling at constant rate.
Lighting: sodium street light stays consistent, neon reflection on
asphalt keeps its shape, no bloom fluctuation.
Style: matches the Tri-X grain of the still, no color drift, no
wardrobe changes.
No cuts.
```

---

## Subject motion vs camera motion — keep them separate

Models confuse the two. Split them into two sentences.

- **Camera motion** = the camera's path (push, pull, orbit, lockoff...).
- **Subject motion** = what the subject's body/face does.

If both, write:
```
Camera move: <named camera move>.
Subject motion: <what the subject does>.
```

Never combine into "subject walks toward the camera as it pulls out". That
produces morphing. Instead:
```
Camera move: slow pull-out, camera recedes at walking pace.
Subject motion: subject walks forward at the same pace, staying roughly
at the same frame-size throughout.
```

---

## Duration tradeoffs

| Length | Good for | Tradeoff |
|---|---|---|
| 3s | Punchline, whip-transition, grid tile | Too short for subject performance |
| 4s | Reaction, lockoff on face | Sweet spot for single emotion |
| 5s | Push-in, pull-out, Ken Burns | Best balance of motion + cost |
| 6-8s | Full shot with motion arc | Risk of drift increases past 6s |
| 10s | Long take, vibe establisher | High drift risk, needs strong still anchor |

Default to **4-5s** unless the shot demands more.

---

## Consistency tricks (stop drift)

1. **Reference image = the still.** Always feed the still as reference, not
   a similar-looking image.
2. **Repeat subject descriptors in the motion prompt.** If the still shows a
   man in a navy jacket, the motion prompt says "same man in the navy
   jacket" — don't trust the model to remember.
3. **Explicit no-change list.** "No wardrobe changes, no room dimension
   changes, no face morph, no background object appearance or disappearance."
4. **Lighting freeze.** Always state "lighting stays consistent, no
   intensity changes, no color temperature shifts".
5. **Single intended motion.** If the subject's face moves, the hands don't.
   If the hands move, the face doesn't. Moving both = moving neither cleanly.

---

## Shot recipes (named, composable)

### Money shot
One hero still, slow push-in, 4-5s. The piece you'd post as a single clip.
```
Camera: slow push-in, 15cm of travel.
Duration: 5s.
Subject: micro-expression read only.
Lighting: frozen.
```

### Vibe establisher
Static lockoff on atmosphere (a place, a wide scene, a still life with
ambient motion). 5-8s.
```
Camera: static lockoff.
Duration: 7s.
Subject: only ambient motion (steam, smoke, rain, reflected lights).
Lighting: frozen, any flicker is only on a named practical light.
```

### Reaction read
Tight on face, barely-there movement, 4-5s. Performance-first.
```
Camera: static lockoff, very slight handheld micro-shake.
Duration: 5s.
Subject: single emotion arc — neutral to target emotion, linear.
```

### Reveal
Pull-out starting tight, ending wide, 5-6s. The "holy shit" shot.
```
Camera: slow pull-out, starts on detail (e.g. hands), ends at medium wide.
Duration: 6s.
Subject: still except for breath / micro-motion.
```

### Match cut pair
Two complementary clips that cut together. Same composition, different
subject or different time. Each 3s static lockoff, then FFmpeg concat.
```
Clip A: static lockoff, 3s, subject <X> in <place>, <lighting>.
Clip B: static lockoff, 3s, subject <Y> in <same place>, <same lighting>.
(Cut hard between them in FFmpeg. Never crossfade.)
```

### Ken Burns hero
One winning still, zoom-and-pan 4-6s. Cheapest pipeline — no video gen
needed, pure FFmpeg (see `memegine edit kenburns`).

---

## Audio (Veo 3 only, in 2026)

Veo 3 generates in-sync native audio from prompts. Grok exposes some of
this for video mode. Prompt structure:

```
Audio: <ambient bed>, <sfx if any>, <dialogue if any>.
```

### Examples
```
Audio: city ambient — distant traffic, light rain, one taxi horn at 3s.
No music, no dialogue.
```
```
Audio: room tone only, hum of the desk lamp, a single phone buzz at 4s.
No dialogue, no music.
```

For music/dialogue that the model can't nail, generate silent video and add
audio via:
- **Music**: Suno free tier, royalty-free library, or your own track
- **Dialogue**: ElevenLabs free tier (if voice cloning is acceptable)
- **SFX**: freesound.org, Pixabay sound library

Use `memegine edit audio <clip> <audio_file> <dst>` to attach.

---

## Failure modes specific to video

| Symptom | Likely cause | Fix |
|---|---|---|
| Face morphs mid-clip | Duration too long or "cinematic" bait | Cut to 4s, remove cinematic, add "no face morph" |
| Wardrobe changes mid-clip | Model forgetting subject | Repeat subject descriptors explicitly |
| Background objects appear/vanish | No constraint on background | Add "no background changes, all objects stay in place" |
| Motion feels speed-ramped | Ambiguous motion phrasing | State exact amount of travel ("15cm over 5 seconds") |
| Camera wobble when you asked for lockoff | Model defaulted to handheld | State "absolute static lockoff, zero camera movement" |
| Lighting flickers | No lighting constraint | Add "lighting frozen, no intensity or color changes" |
| Action doesn't match what you asked | Too many actions in one prompt | One named motion only. Generate multiple clips and cut. |
| Zoom looks jittery / bouncy | Ken Burns without duration tied to fps | Use `memegine edit kenburns` instead — controlled ffmpeg zoompan |

---

## Production pipeline for a 10-second piece

Canonical workflow for an X/Twitter post:

1. **Brief the shot list.** Run `memegine shots "<intent>"` — get 2-3 shots.
2. **Generate hero stills for each shot.** Use still pattern for each.
3. **Pick winners.** Add to `memegine refs` with tags.
4. **Animate each winning still.** img2vid one shot at a time. Generate 3
   versions of each, pick the cleanest.
5. **Stitch.** `memegine edit concat` with hard cuts. Aspect `9:16`.
6. **Grade.** `memegine edit grade --preset moody_film` (or whichever fits
   the mood).
7. **Audio.** If silent, attach music via `memegine edit audio --mode replace`.
   If Veo generated audio, leave as-is.
8. **Caption.** `memegine edit caption` for burned-in lower-third if needed.
   Otherwise the post's text caption does the work.
9. **Export** as-is — H.264 yuv420p, stand-alone MP4, 1080x1920 9:16.
10. **Post.** X compresses. Accept it. Don't over-optimize.

---

## Aspect ratio by platform

- **X main feed**: 16:9 OR 1:1 (1:1 wins more real estate on mobile)
- **X as primary video**: 9:16 autoplays full-height on mobile, highest
  attention capture. Default to 9:16 unless there's a reason not to.
- **X thread media**: 4:5 — more vertical height than 1:1 without going full
  portrait.

`memegine edit aspect <src> <dst> --ratio 9:16 --fit cover` handles all of
these. Use `--fit contain` when cropping would destroy the composition.
