# /codex-update — Update the style codex from session learnings

## Description
After a posting session, update `memegine/data/codex/style.md` with what
worked, what flopped, and what new patterns emerged. The codex compounds
into every future brief — this is the most important ritual.

## Arguments
- `$ARGUMENTS` — Free-text summary of the session or specific learnings.

## Workflow

### 1. Read current codex
```
cd memegine && memegine codex show
```

### 2. Determine what to log
Categorize new learnings into:
- **Proven Prompt Patterns** — prompts that produced winners
- **Kill List** — phrases/patterns that consistently flop
- **Visual DNA** — locked-in aesthetic choices
- **Voice & Tone** — calibration updates (irony dial, etc.)
- **Top performing posts** — reference future briefs against these

### 3. Append entries
For each new learning, run:
```
cd memegine && memegine codex winner "<prompt>" "<why it worked>"
cd memegine && memegine codex flop "<what>" "<why>"
```

Or edit `data/codex/style.md` directly for structural updates (Visual DNA,
Voice, etc.).

### 4. Prune
If the codex is getting long (> 500 lines), summarize older entries:
- Consolidate similar winners under a meta-pattern
- Remove flops older than 3 months if the lesson is learned
- Move resolved items out of active sections

### 5. Present
Show the diff of what you added. Remind the operator: "This is the
compounding memory — every future brief reads this. Month 6 you'll be
radically sharper than month 1."

## Notes
- The codex is intentionally terse. It's NOT documentation — it's a sharp,
  living doc of personal craft knowledge.
- Each entry should be 1-2 lines. If it's longer, it's a meta-pattern that
  belongs in a playbook, not the codex.
- Kill list entries are authoritative — never let a flagged phrase into a
  future caption.
