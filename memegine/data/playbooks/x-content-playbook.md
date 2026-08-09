# X Content Playbook

How to post on X/Twitter in 2026 as a high-craft creator. Opinionated, built
for a single operator (Nunu / @WillumpOnChain) doing 5-30 posts/day with a
memegine-assisted pipeline.

---

## What X actually rewards in 2026

After the post-Elon algorithm changes and the dust settling:

1. **Dwell time on media** — autoplay videos that hold attention for 3+ seconds
   outperform everything. This is the single biggest lever.
2. **Saves > likes** — bookmarks now weight higher than hearts. A piece worth
   saving has a deeper compounding effect.
3. **Replies that stay on-platform** — if your reply thread goes 4+ deep, the
   algorithm boosts the original. Engage your replies.
4. **Not clicking away** — external links demote the post. Keep the payload
   inside X (image, video, reply thread — not a link dump).
5. **Profile clicks** — drives follower conversion. A caption that makes
   people check who posted this earns the most.

**What's dead**: hashtags (noise), engagement-bait frames ("tag a friend",
"who else"), emoji-salad captions, thread hooks with "🧵 a thread", "this is
the way", "gm/wagmi/lfg" discourse.

---

## The three currencies

Every post earns one or more of:

| Currency | Earned by | Compounds into |
|---|---|---|
| **Reach** | Media (video especially) + caption hook | Impressions, followers |
| **Saves** | Genuine insight or reference-worthy content | Long-tail engagement, "the quote tweet" |
| **Replies** | Conversational framing or provocative-but-true takes | Community, algorithm boost |

A balanced feed does all three. Don't only chase reach (hollow) or only
saves (low posting cadence) or only replies (no reach).

---

## Daily cadence for a meme creator

This is a **reference rhythm** — adjust to your time zone and energy.

### Morning (8-11am local): reactive
- 1-2 reaction memes to overnight news / market action
- Fast turnaround: `memegine prompt <intent> -f reaction_shot_meme` → Grok
  → post within 20 min of the event
- Caption: short, punchy, no explanation

### Midday (11am-2pm): volume + variety
- 3-5 posts spread out
- Mix: 1 meme, 1 photoreal piece, 1 insight/commentary (text-only ok here),
  1 quote-post of someone else
- Use `memegine pipeline` to batch-brief these in one session

### Afternoon (2-6pm): hero piece
- 1 high-craft photoreal still OR short video (the "money shot")
- This is the piece you spent real time on — Grok iteration, refs, grading,
  maybe an FFmpeg stitched video
- This is the post that earns saves + profile clicks

### Evening (6-10pm): community + shitposts
- 2-4 casual shitposts, replies, quote-tweets
- Engage your reply threads from earlier in the day
- Light commentary on the day's events

### Late night (10pm-midnight): experiments + lore
- 1 experimental format / lore drop / inside-joke
- Late-night audience is different — they save things

**Target**: 8-15 original posts/day + 5-15 replies/quote-posts. Going above
this risks feed fatigue among your followers.

---

## Post structure — main-feed originals

### The hook sentence
First line = everything. It must:
- Make sense without context (people see it in a feed, scrolling)
- Promise a specific payoff (not "wait for it")
- Avoid the dead openers: "let me tell you", "here's a thought", "so I was
  thinking", "in today's market"

Good opener patterns:
- A confident claim ("The real winners aren't the token holders.")
- A specific noun phrase ("A photo nobody's willing to post.")
- A concrete scene ("3am, market dumping, phone glow on his face.")
- A contrarian-but-true ("Most of this cycle's alpha is public.")

### The payload
Image / video does most of the work. Caption should **not** explain the
image — let it breathe.

### Max lengths
- Main-feed post with media: 0-200 characters ideally. 280 max.
- Main-feed post text-only: 280-500 characters (X allows longer for
  premium; use sparingly)
- Thread post: 280 per tweet, 3-7 tweets total

---

## Caption patterns that work

### The zero-caption
Post image/video, caption is empty or a single period.
When: the image is self-explanatory and strong enough.
Risk: less reach than a captioned version (algorithm likes *some* text).

### The one-liner
Punchline that completes or subverts the image.
Format: `<image of X>` caption: `[one sentence that recontextualizes X]`

Example: image of a trader at 3am → "he's not on call, he's on copium"

### The two-line setup/payoff
```
[setup observation]
[payoff twist]
```
Example:
```
everyone wanted a bear market
nobody wanted to be here for it
```

### The specific detail
A single concrete noun phrase that makes the image land.
Example: image of a chart → "the wick that paid my rent"

### The aside / afterthought
Dry comment, as if caught mid-thought.
Example: image of something serious → "anyway"

### The anti-caption (disclaimer)
Undermine the image intentionally.
Example: photoreal piece → "edited with ms paint"

### The quote (from fiction or history)
A real or fake quote that lands the image.
Example: lore drop → "he was already here."

---

## Dead caption patterns (never)

- "Thoughts?" — dead.
- "Let me know in the comments" — dead.
- "Who else..." — dead.
- "If you know, you know" — overused.
- "Not financial advice" as a joke — dead since 2022.
- "This is the way" — dead.
- "gm", "gn", "wagmi", "lfg", "ngmi" — dead.
- "🚀", "🔥", "💎🙌", any emoji in a serious caption — dead.
- Hashtags — actively hurt reach.
- "Follow for more" — instant unfollow trigger.

---

## Thread anatomy

Threads live and die by the hook tweet.

### The hook tweet (tweet 1)
- A promise + a curiosity gap
- NO emoji, NO "🧵", NO "a thread" label
- People know it's a thread from the reply structure

### Body tweets (2-6)
- Each tweet should be complete on its own (people land on tweet 3 from a
  reply)
- Short paragraphs, no bullet points in the tweet itself (tweet formatting
  is hostile to bullets — use punctuation or line breaks instead)
- One idea per tweet

### Payoff tweet (last)
- The strongest line lives here
- If it's a lessons thread, the payoff is the summary
- If it's a story thread, the payoff is the twist

### When to thread
- When an idea needs 3+ tweets to land.
- When you have a sequence of reveals.
- When one piece of content spawns multiple angles.

### When NOT to thread
- When a single tweet would land it. Threading dilutes.
- When the "thread" is just a list. Make an image instead.
- When you're padding for reach.

---

## Quote-posts vs replies vs main-feed

| Container | Use when | Tradeoff |
|---|---|---|
| **Main-feed original** | Your strongest work, earns followers | Highest craft requirement |
| **Quote-post** | Reacting to someone else's post with your own spin | Borrows reach, may cede credit |
| **Reply** | Extending someone's post, or in your own thread | Lowest reach, highest community |
| **Quote-post of yourself** | Amplifying an older post that's resurfacing | Rarely lands well, feels desperate |

Rule: your first post of the day should be main-feed original, not a quote
or reply. The algorithm notices your first post and seeds its reach.

---

## Reply hygiene

- Engage replies within the first 60 minutes. The algorithm boosts posts
  with early reply activity.
- Never argue with bad-faith replies — reply once, move on, or mute.
- Reply to genuine questions substantively. That reply is more valuable to
  the commenter than the original post.
- Quote-post only exceptional replies. Most should stay as replies.
- Block generously. One bad reply chain can cost you hours.

---

## Posting times that matter (2026, approximate)

These are population-level; verify with your analytics after 2 weeks.

| Window (local) | Audience | Good for |
|---|---|---|
| 7-9am | Morning commute | Reactive content to overnight events |
| 11am-1pm | Lunch break | Volume posts, casual stuff |
| 2-4pm | Post-lunch afternoon | Hero posts, long-form |
| 5-7pm | Post-work / pre-dinner | Memes, commentary |
| 9-11pm | Night audience | Lore drops, experimental, saves |

Avoid: 2-5am (unless you're targeting a specific timezone), and dead zones
between 1-2pm and 7-8pm (dinner time).

Space posts 45-90 minutes apart. Posting 5 in 10 minutes triggers shadow-
damping.

---

## The 30-a-day question

Can you realistically do 30 posts/day?

**Yes, but only if:**
- You batch-brief in 2 sessions per day (morning + afternoon) using
  `memegine pipeline` to produce 10+ briefs at once.
- The mix is heavily weighted toward quick reactive content + quote-posts
  + replies, with 1-3 hero pieces per day max.
- You accept that not every post earns reach — volume is for currency
  diversification (saves + replies), not every-post virality.
- You use the style codex to prevent repetition — nothing kills a feed like
  the same joke structure three times.

**No, if:**
- You're manually typing each caption without templates.
- You're generating images one-at-a-time for each post.
- You're not batching reference-library work (your best outputs compound
  the system's taste).

**Realistic target**: 10-20 posts/day sustained, 25-30 on event-driven peak
days.

---

## Kill list — phrases that age poorly

These were fine in 2021-2023 but now read as past-their-time:

- "Vibes"
- "Built different"
- "Main character energy"
- "It's giving..."
- "No thoughts, just vibes"
- "This is the way"
- "We're so back"
- "It's over"
- "Chad [X] vs virgin [Y]" (the template still works; the phrase is dead)
- "Touch grass"
- "Ratio'd"
- "Cringe" as a standalone insult
- "Based" (overused)
- "Mid"
- "Fire" 🔥
- Anything that starts with "imagine..."

When something from this list enters your draft, replace it with a specific
detail. Dead phrases are placeholder energy.

---

## Kill list — engagement-bait frames

These hurt reach in 2026:

- "Tag someone who needs to see this"
- "Who else thinks..."
- "Retweet if..."
- "Like if..."
- "Follow for more..."
- "Let me know in the comments"
- "Drop a 💎 if..."
- "Thread 🧵 below"
- "A thread:"
- "1/" numbering on threads (X threads now auto-number visually)

---

## Measurement — what to actually track

Ignore vanity metrics. Track these:

1. **Saves per 1000 impressions.** Reference-worthy content lives in saves.
2. **Profile clicks per 1000 impressions.** Follower conversion.
3. **Reply depth.** Avg reply thread length on your main posts.
4. **7-day follower delta.** Raw growth.
5. **Hero piece hit rate.** Of your craft-intensive posts, what % cleared
   your P75 save count?

Export weekly via X's analytics. Log to your style codex under a new
"Measurement" section.

---

## Monthly reset ritual

First day of each month, do this:
1. Read back your top 5 posts of the previous month. Log what made them work
   to `style.md -> Proven Prompt Patterns` and `Top performing posts`.
2. Read back your bottom 5 posts. Log to `Kill List`.
3. Check your format library — which formats produced the top posts? Bump
   their usage. Which didn't land any? Consider retiring them.
4. Update your daily rhythm if your analytics suggest a different peak
   window than last month's.

This is the system compounding. Month 6 you will be radically sharper than
month 1.
