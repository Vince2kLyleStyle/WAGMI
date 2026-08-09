# Meme Typography Playbook

Text-on-image is where 80% of AI memes die. Either the model garbles letters,
the font fights the image, or the placement violates mobile safe zones. This
is the craft reference to prevent that.

---

## Font choice by format

| Format | Font | Why |
|---|---|---|
| Classic reaction meme (Drake, two-panel) | **Impact Bold** | The meme canon. White + 3-5px black stroke. Nothing else reads as "meme" on sight. |
| Modern / ironic meme | **Helvetica Bold Condensed** or **Arial Black** | Reads as "designed by someone", less canonical, more 2020s. |
| Screenshot / fake news / terminal | **Serif broadsheet** (Georgia / Times) for headline, **monospace** for body | Looks like real journalism / a real terminal. |
| Cope chart / fake bloomberg | **Monospace** throughout (IBM Plex Mono, JetBrains Mono) | Terminal data aesthetic. |
| Cozy / wholesome / lore drop | **Handwritten** (Permanent Marker, Caveat) or **humanist serif** | Breaks the meme-canon feel, reads as earnest. |
| Announcement / "big news" | **Condensed sans** (Oswald Bold, Bebec Neue) | Poster aesthetic, vertical compression suits short copy. |
| Typewriter / noir | **American Typewriter** / Courier Bold | Period feel, works for lore drops. |

**Default** for any reaction or two-panel meme: **Impact Bold, white, 3-5px
black stroke.** Deviate only when you have a reason.

### What you ask Ideogram for
- "Impact Bold, white with 3px black stroke"
- "Helvetica Bold Condensed, white on translucent black bar"
- "Courier Bold, black on cream paper background"
- "Georgia serif headline, black on white"

Ideogram recognizes font family names. Don't say "a cool font" — name one.

---

## Text placement — top, bottom, center, inside

### Top caption (classic two-panel)
- **Use when**: setting up the joke (the "expectation" or "situation" panel)
- **Safe zone**: start text at least 40px from top edge; leave 30% of panel
  height for text including margins
- **Reads**: authoritative, framing

### Bottom caption (classic two-panel)
- **Use when**: delivering the payoff (the "reality" / punchline panel)
- **Safe zone**: end text at least 40px from bottom edge
- **Reads**: reveal, joke

### Dead-center (headline poster)
- **Use when**: single-panel reaction where image IS the caption's subject
- **Safe zone**: keep text within the middle 60% of both axes
- **Reads**: poster / announcement

### Inside a panel (speech bubble, label, chart annotation)
- **Use when**: text is part of the scene (wojak bubbles, product label,
  chart caption)
- **Needs**: a container — speech bubble, label, box, banner
- **Reads**: diegetic — the text lives inside the world

### Right-aligned (Drake template)
- **Use when**: Drake preference layout or similar side-by-side
- **Safe zone**: 5% right margin, vertical center of each panel

---

## Mobile safe zones on X

X crops images on the feed before tap-to-expand. Your caption MUST be
readable in the cropped view.

- **1:1 post**: displayed in full on the feed. Safe zone = full image, but
  avoid the outer 40px (icons / reply UI can overlay).
- **9:16 post**: displayed as a centered crop, typically showing the middle
  80% of vertical height. Put captions in the **middle 70% vertical zone**.
- **16:9 post**: displayed at reduced height; text smaller than 48pt
  illegible on mobile. Go bigger.

**Rule of thumb: if text isn't readable on a 5-inch phone at arm's length,
it isn't working.**

---

## Stroke, shadow, box

### White with black stroke (3-5px)
Classic meme look. Works on any background. Impact or Arial Black only.

```
font_color=white
stroke_color=black
stroke_width=3 (for images < 1200px wide)
stroke_width=5 (for images 1200-2000px wide)
```

### Drop shadow (soft, 10-20px radius)
Modern / designed feel. Softer than hard stroke. Use with Helvetica Bold or
similar. Shadow should be subtle — if you can see it clearly, it's too dark.

### Translucent box
When an image is busy and neither stroke nor shadow reads, put the text on
a translucent black box. Rules:
- Opacity 40-60% black behind text
- 12-24px padding around text
- Box width = text width + padding (don't make it full-image-width unless
  it's a lower-third)

### Lower-third strip
Horizontal black bar across the bottom quarter of the image with text in it.
News-broadcast aesthetic. Good for "BREAKING" or headline posts.

### What NEVER works
- Black text on a photo without any container — unreadable on ~50% of images.
- Gradient text — screams Photoshop 2008.
- Text with a glow / bloom / outer radius — screams AI-generated.
- Multiple strokes stacked — screams Canva beginner.

---

## Caption size rules

Size relative to image width is what matters, not absolute pixel values.

| Image width | Classic meme font size | Modern caption size |
|---|---|---|
| 720px (small) | 48-60px | 36-44px |
| 1080px (standard X) | 72-96px | 54-68px |
| 1350px (4:5 X) | 90-110px | 64-80px |
| 1920px (16:9) | 108-128px | 80-96px |

`memegine edit caption` auto-sizes at `image_width // 18` which lands in
this range for most targets. Override with `--size` when you need a specific
size.

---

## Line breaking

- **Never auto-wrap** on a word boundary that breaks the joke's rhythm.
- Max 4-6 words per line for meme captions.
- Max 2 lines for top or bottom captions (more = too much text, scroll past).
- For two-line setup → payoff, keep both lines similar word count so they
  balance visually.

Good:
```
THE PROBLEM
MY REFUSAL TO PROCESS IT
```

Bad:
```
THE BIG PROBLEM IS THIS THING
I AM NOT GOING TO DEAL WITH IT
```
(too many words, line length mismatched)

---

## Language in captions

Caption length categories:
- **Zero words**: the image carries the post. X caption below does the
  talking. Often the strongest move.
- **1-3 words**: punchline or label ("me", "cope", "not financial advice").
- **4-6 words per line**: standard two-line meme ("when he tells you he
  sold" / "at the top of the candle").
- **7+ words**: almost never. Exception: fake-news headline format where the
  headline IS the joke.

### What to avoid
- Emojis baked into the image caption. Never. Emojis go in the X post caption
  below, not in the image.
- Hashtags in the image. Never.
- "@" mentions in the image. Never.
- Trademark symbols, copyright, "©". Read as clip-art.
- "lol", "literally", "vibes", "fr fr", "no cap" — dated.
- "gm", "wagmi", "lfg" — dead in 2026.

---

## Anti-patterns: AI-generated meme tells

These scream "this was made by a model, not a person":

1. **Garbled letters** — especially on peripheral/small text. Fix: use
   Ideogram routing, quote verbatim, shorter captions.
2. **Wrong font fallback** — text that SHOULD be Impact rendering as some
   bastardized serif. Fix: name the font explicitly.
3. **Over-smooth text edges** — as if the text is part of the pixel grid.
   Fix: ask for "crisp text edges, no anti-alias bloom".
4. **Text duplicated in two places** — model tried to render the prompt
   caption twice. Fix: use negative list — "no duplicate text, no redundant
   captions, caption appears ONCE at <position>".
5. **Text inside the subject's body** (letters on their face, etc.) — model
   didn't understand "overlay" vs "in the scene". Fix: say "caption overlay
   ON TOP OF the image, not within the scene".
6. **Background bleeding through the text stroke** — stroke too thin. Fix:
   use at least 3px stroke for images ≥ 1080px wide.
7. **Caption in a different language** — prompt was vague. Fix: quote text
   in double quotes explicitly.
8. **Random watermark / logo "MEME" in the corner** — model hallucinated
   branding. Fix: add "no watermark, no logo, no brand marks" to negative.
9. **Emoji that was never requested** — model over-decorated. Fix: "no
   emoji, no emoticons, no decorative symbols".
10. **Fancy background "depth" effects** — gradients, particles, light leaks.
    Fix: state "flat, no gradient background, no light leaks, no particle
    effects".

---

## Workflow for meme text

1. Write your caption text **by hand** first. If it reads flat on its own,
   no meme craft can save it.
2. Pick a font family from the table above.
3. Prompt Ideogram (via Grok) with the exact quoted caption, named font,
   named placement, named stroke.
4. Generate 4 variants. Pick the cleanest rendering.
5. If text is still garbled after 4 tries, generate the IMAGE WITHOUT TEXT
   via any Grok model, then burn text locally with `memegine edit caption`
   (uses Pillow, never garbles).

**The fallback is important.** Any image you love but whose text came back
ugly can be regenerated without text, then captioned locally with perfect
fidelity.

---

## Reference ideal meme text examples

- **Drake two-panel**: 4 words each side, right-aligned, Arial Black white,
  no stroke (white bg).
- **Classic two-panel reaction**: Impact bold, white with 3px black stroke,
  top and bottom, 4-5 words per line.
- **NPC wojak row**: small speech bubbles inside the scene, black text in
  comic-sans-style inside white bubbles with black outlines.
- **Fake news screenshot**: Georgia serif headline, black on white, 8-14
  words; subhead in same family regular weight.
- **Cope chart / fake bloomberg**: IBM Plex Mono throughout, yellow on black
  for the ticker-row, white for body, red/green for data.
- **Lore drop**: no text in image. Caption carries it.
