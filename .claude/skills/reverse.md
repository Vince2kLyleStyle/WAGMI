# /reverse — Reverse-engineer an image's look into a Grok prompt

## Description
Given an image (screenshot of a post, reference photo, saved piece), produce
a prompt that would recreate its LOOK in Grok — named lens, named film stock,
named lighting, composition rule, recreate_prompt ready to paste.

## Arguments
- `$ARGUMENTS` — Path to an image file (absolute or relative) + optional
  free-text context like "for a trader portrait" or "I liked the lighting".

## Workflow

### 1. Confirm the file exists
Read the file to confirm it's an image. If the path is wrong or it's not an
image, ask for the correct path.

### 2. Assemble the brief
```
cd memegine && memegine reverse <path> --context "<context>"
```

### 3. Analyze the image in this session
You have vision. Open the image via Read (for local files). Look for:
- Lighting quality (hard vs soft, key direction, color temperature)
- Depth of field (estimate focal length by compression)
- Grain / sensor look (film vs digital, grain structure)
- Color palette (extract 3-5 dominant colors)
- Composition (which rule is in play)
- Mood dials (warmth, contrast, grain, desaturation as 0-1)

### 4. Output the JSON per the SYSTEM prompt
Include:
- `look_description` — camera-first vocabulary
- `estimated_lens`
- `estimated_film_stock_or_sensor_look`
- `lighting` — named setup, not adjectives
- `time_of_day_or_condition`
- `color_palette` — 4 hex codes
- `composition` — rule name + placement
- `mood_dials` — numeric 0-1
- `recreate_prompt` — production prompt ready for Grok
- `banned_words_check` — confirm none are present

### 5. Self-lint
```
cd memegine && memegine lint "<recreate_prompt>"
```
If fail, fix and re-lint.

### 6. Offer to save to reference library
If the operator likes the analysis, suggest:
```
memegine refs add <path> --tags "<tags_from_analysis>" --notes "<why>" --prompt "<recreate_prompt>"
```

## Notes
- You cannot actually access URLs for random images unless they're local.
  If the operator pastes a URL, ask them to save locally first.
- The point of reverse-engineering is to grow the reference library — these
  are the shoulders we stand on.
