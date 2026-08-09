# Morning questions

Things I made judgment calls on while working autonomously. Review and
confirm/redirect when you have a minute.

## Open questions

(none yet — this file grows when there's genuine ambiguity)

## Decisions I made without you (so you know)

- **Default aspect ratio = 9:16** for all video renders. Reasoning: X
  autoplay is full-height on mobile; highest attention capture. Override
  with `--ratio` flags.
- **Default length sweet-spot for prompts = 40-150 words.** Scorer
  penalizes outside this. Felt right across the sample prompts I
  tested; revise if you find shorter/longer prompts land better.
- **Scorer grade cutoffs: A≥85 / B≥70 / C≥55 / D<55.** CLI exits non-zero
  below 55 so it can gate scripts.
- **Capture queue lives in `data/logs/captures.jsonl`** (same folder as
  brief archive) — kept them together because they're both append-only
  operator trails.
- **Telegram whitelist is env-var driven** (not stored in repo). Set
  `MEMEGINE_TELEGRAM_OPERATOR_CHAT_ID` to your numeric Telegram user ID.
  The bot ignores every other chat.
- **SFX whoosh uses static bandpass + envelope** (instead of a
  time-varying sweep). FFmpeg's bandpass doesn't accept time-varying
  `f`. Approximation works for 0.2-0.5s whooshes. If you want a true
  pitch sweep, we can add `asetrate` post-processing later.
- **Playbooks auto-loaded for each brief kind**:
  - image briefs: grok-imagine-patterns + meme-typography
  - video briefs: + video-img2vid-patterns + music-edit-patterns
  (x-content-playbook is referenced but not auto-included — it's about
  posting strategy, not generation)
- **Format library size target: 25-30**, currently at 26. Growing
  further will require real use signal. I'm not inventing speculative
  formats.
- **Grading preset count: 19** (10 original + 9 added overnight). I
  stopped here because more start feeling redundant. Adding one is
  cheap when you find a look you like.

## Things I did NOT do (and why)

- **Did not write an end-to-end integration test yet** — it's listed
  in Tier 5 of AUTONOMOUS-PROMPT.md. Waiting until the music-edit
  text-on-beat feature lands so the integration covers the whole
  current surface in one pass.
- **Did not implement Telegram inline keyboards** — the bot's current
  surface is fully functional with text commands. Keyboards are polish,
  deferred to Tier 2.
- **Did not scrape live research for playbooks** — two research agents
  timed out and the third stalled. I wrote the playbooks from first
  principles instead; they're opinionated but grounded. If you find
  patterns that contradict what's in them, update the codex and the
  playbook together.

## What I'd ask you to do first when you're back

1. Fill 5-10 lines in `data/codex/style.md` under "Voice & Tone" and
   "Visual DNA". Even rough notes move the system significantly.
2. Run `memegine formats` and flag any format slug that feels wrong
   for your brand — I'll prune.
3. Try `/piece <any idea>` in Claude Code. Watch where it falls short.
   That's the next tuning signal.
