# Grok Imagine Prompt Patterns

Craft bible for prompting the image models Grok exposes via its Imagine
interface (accessible through X Premium). Read this before every new brief.

> This is opinionated and current as of early 2026. Update as patterns change.
> When something lands, add it here. When something flops, add it to the Kill
> List in `data/codex/style.md`.

## The models Grok routes between

- **Nano Banana** (Gemini 2.5 Flash Image derivative): character consistency,
  fast edits, photoreal with good skin, moderate text rendering. Best for
  iterating on a subject across scenes and for in-image edits ("make him cry",
  "change his wardrobe").
- **Aurora** (xAI in-house): painterly to photoreal range, strong with stylized
  and cinematic-feel scenes. Weak on text. Good for "hero art" pieces.
- **Flux Pro 1.1 Ultra** (routed occasionally): cleanest photoreal skin, best
  for portrait work where hands/eyes matter. Slower than the above.
- **Ideogram 3** (for text-heavy): unmatched typography rendering. Always the
  call when the meme's text lives inside the image.

You don't pick the model directly most of the time — Grok routes based on your
prompt's shape. To bias routing, use the cues below.

---

## Universal rules (every model)

1. **Banned words.** NEVER use: `cinematic`, `epic`, `stunning`, `beautiful`,
   `masterpiece`, `4k`, `8k`, `ultra-realistic`, `hyperrealistic`,
   `award-winning`, `trending on artstation`, `breathtaking`, `ethereal`,
   `highly detailed`, `intricate details`, `perfect composition`, `professional
   photography`. These are meaningless, crowd the prompt, and mark output as
   AI slop.

2. **Specificity beats superlatives.** Instead of "cinematic lighting", write
   "hard directional window light from camera-right, shadow side dominant".

3. **Name the gear.** Lens (35mm f/1.4, 85mm f/1.2, 24mm anamorphic), film
   stock (Cinestill 800T, Portra 400, Tri-X pushed to 1600), or sensor look
   (Leica Q3, medium format digital). The model has seen these names a million
   times — they are the most efficient quality levers in English.

4. **State time and condition.** "3am, practical-lit street" beats "night".
   "Overcast noon, soft diffuse light" beats "daylight". "Golden hour, low sun
   raking camera-right" beats "warm light".

5. **State composition by rule name.** "Rule of thirds, subject left",
   "centered medium close-up", "leading lines from foreground", "negative space
   right half", "low angle, 30° below subject eyeline".

6. **End with a negative list.** Close every prompt with what NOT to render:
   `no extra fingers, no warped text, no logo watermarks, no lens flare, no
   plastic skin, no HDR halos`.

7. **One subject, one scene, one mood.** Prompts that try to do three things
   at once produce mush. Split into three generations instead.

8. **Length: 60-120 words is the sweet spot.** Shorter tends to default to
   AI-cliché stock. Longer tends to fight itself. Aim for one long sentence or
   three tight clauses.

---

## Photoreal portrait pattern (Nano Banana / Flux / Aurora)

```
<subject, 1 sentence>, shot on <camera and lens>, <film stock or sensor look>,
<lighting setup>, <location and time of day>, <wardrobe and props>,
<composition rule>, <mood keyword or two>, photoreal, no CGI,
sharp focus on eyes, natural skin texture with visible pores,
<negative list>.
```

### Example A — night trader

```
Trader in his late 20s at a home office, mid-emotion read as he looks at
a phone screen, shot on a Canon EOS R5 with 85mm f/1.2, Cinestill 800T
look with subtle halation on the highlights, single practical desk lamp
camera-right as the only light source, 3am, overcast outside dark window,
grey hoodie and black wired earbuds, rule of thirds subject left, desk
clutter right third, mood tense and exhausted, photoreal, no CGI, sharp
focus on eyes, natural skin pores visible, no warped text, no extra
fingers, no plastic skin, no HDR.
```

### Example B — street photograph

```
Candid street photograph of a man lighting a cigarette on a narrow
Tokyo side street, shot on a Leica Q3 at 28mm f/1.7, Tri-X pushed to 1600
look with tight grain, available sodium-vapor street light from above and
neon-sign bloom from a shop on the left, rainy evening after 10pm, black
leather jacket with rain droplets, rule of thirds subject left, leading
lines from wet asphalt reflections, mood solitary and unhurried, photoreal,
no posing, no eye contact with camera, no extra fingers, no lens flare.
```

### Why this works
- Every specifying phrase lands on a known model concept (the name of the
  lens, the name of the stock, the name of the rule).
- The mood is two words at the end, not scattered throughout.
- The negative list cuts off common failure modes before they happen.

---

## Text-in-image meme pattern (Ideogram 3 via Grok)

The model must render text as text, not as blurry approximations of letters.
This is the single hardest thing current models do, and Ideogram 3 is the only
one that consistently nails it.

### Rules
- **Quote the text verbatim inside double quotes.** Do not paraphrase.
- **Name the font family.** "Impact bold", "Helvetica Bold condensed",
  "Arial Black", "monospace terminal", "serif broadsheet headline".
- **State placement.** Top-center / bottom-center / dead-center / right-
  aligned / inside a panel.
- **State stroke/shadow.** "White text with 3px black stroke" is the classic
  meme look. "Black text with drop shadow" reads more modern.
- **Keep captions short.** 2-8 words per line. More = smaller text = risk
  of garbling.
- **Avoid rare characters.** `&`, `#`, `@`, curly quotes all occasionally
  garble. Use plain ASCII when possible.

### Template
```
<image scene description, 1 sentence>. Caption overlay
<placement>: "<exact caption text>" in <font> with <stroke/shadow>.
<negative list>, no warped letters, no fake-text approximation.
```

### Example — two-panel setup/payoff
```
Two-panel meme, stacked vertically, equal panels, thin black divider.
Top panel: a chart labeled "Q4 outlook" with a line crashing dramatically.
Bottom panel: the same chart one week later, line unchanged but with a
sticky note labeled "copium" on top. Caption overlay top center: "THE
PROBLEM" in Impact bold white with 3px black stroke. Caption overlay
bottom center: "MY REFUSAL TO PROCESS IT" in Impact bold white with 3px
black stroke. 1080x1350 vertical. No warped letters, no extra text, no
watermarks.
```

---

## Character consistency pattern (Nano Banana)

Nano Banana is the best of the Grok-routed models for "same character,
different scenes". Character consistency is fragile — one wrong word and
the face drifts.

### Rules for the first generation
- Lock the character with **5-7 stable descriptors** that you will repeat
  VERBATIM in every subsequent prompt: height, build, face shape, hair color
  + style, eye color, a distinguishing mark (scar, stubble, glasses).
- Name the wardrobe explicitly: "navy Carhartt work jacket, dark jeans,
  grey Merrell boots" — repeat verbatim.
- Lock the **color palette** in the first generation and reuse.

### Rules for subsequent generations
- Paste the character block verbatim. Do not rephrase.
- Change only the scene / action / camera / lighting.
- If the face drifts, send the first generation back to Grok as a reference
  image with "same character as reference, new scene: ..."

### Example — character block you reuse
```
Character (do not modify): mid-20s male, 5'10", wiry build, square jaw,
short dark brown hair parted right, green eyes, small scar above left
eyebrow, 3-day stubble. Wardrobe: navy Carhartt work jacket over a plain
grey tee, dark indigo jeans, scuffed brown Red Wing boots. Palette: cool
greys, navy, warm brown leather accents.
```

Prepend that block to every scene prompt.

---

## Reference image workflow

Grok accepts a reference image alongside the prompt via its UI. Use this for:

1. **Style transfer** — upload a film still / photo whose LOOK you want.
   Prompt: `match the lighting, palette, and grain of the reference; new
   subject: <description>`.
2. **Character reference** — upload your first successful generation when
   spinning up scene 2.
3. **Composition reference** — upload a moodboard tile of the exact framing
   you want. Prompt: `match the composition of the reference`.
4. **Refinement** — upload a generation you're 80% on and prompt the change:
   `same image, change only the wardrobe to a black bomber jacket`.

The tool's `memegine refs` library is where you curate your own reference
set. Tag them aggressively so the codex can cite them later.

---

## Common failure modes and how to prevent them

| Failure | Cause | Fix |
|---|---|---|
| Plastic / waxy skin | "photorealistic", "masterpiece", "8k" bait | Delete those words. Add "natural skin texture with visible pores". |
| Warped/extra fingers | Hands asked to do complex actions | Keep hands simple (at side, in pocket, holding one simple object). Add "no extra fingers" to negative list. |
| Garbled text | Too much text, rare characters, wrong model routing | Keep text ≤ 6 words per line. Quote it verbatim. State "Ideogram style" to bias routing. |
| Generic AI-portrait face | No specificity on subject | Add an age range, a distinguishing feature, a specific emotion word. |
| "Cinematic" over-contrast look | Banned word in prompt | Remove banned words. Name the actual lighting setup. |
| Plasticky CGI background | Asked for "beautiful scenery" | Name the actual location + time: "a shop interior in 1970s Tokyo, late afternoon". |
| Style drift across variants | Changing more than one variable | Change one axis at a time (lens / time / lighting / wardrobe). See `memegine variants`. |
| Eyes out of focus | Lens wider than 50mm with small subject | For portraits, use 50mm+ and add "sharp focus on eyes". |
| Subject feels posed / stock-photo | Asked for "smiling woman" | Replace with an action or emotion cue: "laughing at an off-screen joke", "mid-sentence, eyes looking away". |

---

## Routing cues (bias Grok to the right model)

- **Want Ideogram (text)**: mention "Ideogram-style text rendering",
  "Impact bold caption", "typography-accurate", "magazine headline".
- **Want Flux (skin/hands)**: "Flux Pro ultra photoreal, natural skin
  texture, clean hands".
- **Want Aurora (stylized/painterly)**: "painterly", "oil-paint texture",
  "editorial illustration", "magazine cover art".
- **Want Nano Banana (edit/consistency)**: include a reference image AND
  say "keep the character from the reference, new scene".

---

## Quick-reference prompt skeleton

```
<subject in one sentence, with specific emotion or action>,
shot on <camera + lens>, <film stock or sensor look>,
<named lighting setup>, <location + time of day + weather>,
<wardrobe / props kept simple>, <composition rule>,
<mood, one or two words>,
photoreal, <any model-routing cue>,
<negative list: no extra fingers, no warped text, no plastic skin, no HDR halos, no lens flare>.
```

Memorize the skeleton. Your first draft of any prompt fills this, then the
Director refines it.
