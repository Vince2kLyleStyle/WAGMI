# /caption — Write X captions that don't sound AI

## Description
Given a finished piece (image or video concept), generate 3 caption options
for X/Twitter that pass the voice rules: no emojis, no hashtags, no
engagement-bait, no dead phrases.

## Arguments
- `$ARGUMENTS` — A short description of what's in the piece, e.g.
  "photoreal trader at 3am, market dumping" or path to an image in the refs
  library.

## Workflow

### 1. Assemble the copy brief
```
cd memegine && memegine copy "<concept>" --kind image
```
(or `--kind video` if the piece is a video)

This prints a SYSTEM + USER block including the current style codex and the
copywriting rules.

### 2. Produce the JSON yourself
Act as the Copywriter per the SYSTEM prompt. Output the JSON:
- `captions`: 3 options — short (≤8 words), medium (≤20 words), two_line_punch
- `alt_text`: 100-160 char accessibility description
- `hashtag_warning`: default "none, hashtags hurt reach"
- `reply_hook_if_needed`: optional follow-up reply

### 3. Self-check
Confirm each caption:
- NO emoji (not even a period replaced with a ✨)
- NO hashtags
- NO "gm", "wagmi", "lfg", "thoughts?", "let me know", "who else"
- Doesn't explain the image — leaves room

### 4. Present
Print the 3 captions in order. The operator picks one and posts.

If the concept implies a thread (3+ related ideas), offer a brief thread
structure instead: hook tweet + 2-3 body tweets + payoff.

## Notes
- The style codex lives at `memegine/data/codex/style.md`. Read it for
  voice — if the codex has specific "don't use X" rules, honor them.
- Kill list entries in the codex are authoritative. Never generate a caption
  containing a kill-list phrase.
