"""Telegram bot — the cockpit.

Thin wrapper around python-telegram-bot that exposes memegine commands via
a single-operator Telegram chat. The bot:

- only responds to your whitelisted chat id (config.telegram_operator_chat_id)
- routes each command to the underlying memegine module
- replies with the full brief (SYSTEM + USER blocks) so you can paste into
  Claude.ai on mobile, or forward to your desktop session
- stores every incoming intent in the idea queue (capture.py) so nothing
  is lost

Commands:
  /piece <intent> [image|video] [format]
  /brief <intent> [-f format]
  /shots <intent>
  /caption <concept>
  /capture <intent>               -- quick-save to idea queue
  /queue                          -- list queued ideas
  /formats                        -- list available formats
  /codex                          -- show style codex

Only runs if TELEGRAM_BOT_TOKEN and MEMEGINE_TELEGRAM_OPERATOR_CHAT_ID are
set. The module imports safely even when python-telegram-bot isn't
installed so tests can import without the dep.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from . import (
    archive,
    copy_writer,
    prompt_engine,
    shot_list,
    style_codex,
)
from .config import settings


@dataclass
class OperatorOnly:
    """Check that the sender is the whitelisted operator chat id."""
    operator_chat_id: int

    def __call__(self, update: Any) -> bool:
        try:
            chat_id = update.effective_chat.id
        except AttributeError:
            return False
        return int(chat_id) == int(self.operator_chat_id)


def _fmt_brief_for_telegram(system: str, user: str, title: str) -> list[str]:
    """Split brief into Telegram-sized chunks (4096 char limit per message)."""
    header = f"*{title}*"
    sys_block = "SYSTEM:\n```\n" + system[:3500] + ("…" if len(system) > 3500 else "") + "\n```"
    user_block = "USER:\n```\n" + user[:3500] + ("…" if len(user) > 3500 else "") + "\n```"
    if len(system) > 3500 or len(user) > 3500:
        note = "\n\n(brief truncated — see `data/logs/briefs-*.jsonl` for full version)"
    else:
        note = ""
    return [header, sys_block, user_block + note]


# ---------------------------------------------------------------------------
# Command handlers (pure — take strings, return list[str] reply messages).
# These are unit-testable without a running Telegram client.
# ---------------------------------------------------------------------------


def handle_piece(intent: str, kind: str = "image", format_slug: str | None = None) -> list[str]:
    """Handle /piece — assemble a full piece brief."""
    from . import pipeline as pipeline_mod
    try:
        bundle = pipeline_mod.build(
            intent,
            kind=kind,
            format_slug=format_slug,
            include_copy=True,
        )
    except ValueError as e:
        return [f"❌ {e}"]
    lines = [
        f"*Piece bundle {bundle.id}*  kind={bundle.kind}",
        f"folder: `{bundle.folder}`",
        "Briefs produced:",
        *[f"  • {name}" for name in bundle.briefs],
        "\nOpen each .md in order, paste into Claude Code or Claude.ai",
    ]
    return ["\n".join(lines)]


def handle_brief(intent: str, format_slug: str = "photoreal_portrait") -> list[str]:
    """Handle /brief."""
    try:
        system, user = prompt_engine.assemble_offline_prompt(intent, format_slug)
    except ValueError as e:
        return [f"❌ {e}"]
    archive.save(kind="prompt", intent=intent, system=system, user=user, format_=format_slug)
    return _fmt_brief_for_telegram(system, user, f"Prompt brief — {format_slug}")


def handle_shots(intent: str) -> list[str]:
    """Handle /shots."""
    system, user = shot_list.assemble_offline_shot_list_prompt(intent)
    archive.save(kind="shots", intent=intent, system=system, user=user)
    return _fmt_brief_for_telegram(system, user, "Shot list brief")


def handle_caption(concept: str, kind: str = "image") -> list[str]:
    """Handle /caption."""
    system, user = copy_writer.assemble_offline_copy_prompt(concept, kind)
    archive.save(kind="copy", intent=concept, system=system, user=user)
    return _fmt_brief_for_telegram(system, user, "Caption brief")


def handle_capture(intent: str) -> list[str]:
    """Handle /capture — save rough thought to the idea queue."""
    from . import capture
    entry = capture.add(intent)
    return [f"✓ captured `{entry.id}` at {entry.created_at[:19]}"]


def handle_queue() -> list[str]:
    """Handle /queue — list pending captures."""
    from . import capture
    pending = capture.list_pending()
    if not pending:
        return ["idea queue is empty"]
    lines = ["*Idea queue (pending)*"]
    for e in pending[:20]:
        lines.append(f"  `{e.id}`  {e.intent[:80]}")
    return ["\n".join(lines)]


def handle_formats() -> list[str]:
    """Handle /formats."""
    fmts = prompt_engine.load_formats()
    lines = ["*Available formats*"]
    for f in fmts:
        lines.append(f"  `{f.slug}`  ({f.kind})")
    return ["\n".join(lines)]


def handle_codex() -> list[str]:
    """Handle /codex — return current style codex (truncated)."""
    text = style_codex.read()
    if not text:
        return ["codex is empty"]
    chunk = text[:3500]
    return [f"*Style codex*\n```\n{chunk}\n```"]


def handle_get_group_id(chat_id: int) -> list[str]:
    """Return the current chat ID (for debugging/setup)."""
    return [f"Chat ID: `{chat_id}`"]


# ---------------------------------------------------------------------------
# Argument parsing — reused by both live bot and tests.
# ---------------------------------------------------------------------------


def parse_piece_args(args: str) -> tuple[str, str, str | None]:
    """Parse a /piece argument string.

    Returns (intent, kind, format_slug). Default kind='image', format=None.
    Recognized flags: 'image' / 'video' as bare word; '-f <slug>'.
    """
    kind = "image"
    format_slug: str | None = None
    tokens = args.split()
    intent_tokens: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("image", "video"):
            kind = t
        elif t in ("-f", "--format") and i + 1 < len(tokens):
            format_slug = tokens[i + 1]
            i += 1
        else:
            intent_tokens.append(t)
        i += 1
    return " ".join(intent_tokens).strip(), kind, format_slug


def parse_brief_args(args: str) -> tuple[str, str]:
    """Parse /brief intent + optional -f format."""
    tokens = args.split()
    format_slug = "photoreal_portrait"
    intent_tokens: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-f", "--format") and i + 1 < len(tokens):
            format_slug = tokens[i + 1]
            i += 1
        else:
            intent_tokens.append(t)
        i += 1
    return " ".join(intent_tokens).strip(), format_slug


# ---------------------------------------------------------------------------
# Raid moderation — group chat handlers, Twitter analysis, gallery integration
# ---------------------------------------------------------------------------

import re
import json
from typing import Optional
import httpx
from .raid_tracker import log_raider_action, get_raider_stats, get_leaderboard


SCAM_PATTERNS = [
    r'\b0x[a-fA-F0-9]{40}\b',           # ETH wallet
    r'[1-9A-HJ-NP-Za-km-z]{32,44}',     # Solana wallet (rough)
    r'\bdm\s+me\b',
    r'\bfree\s+crypto\b',
    r'\bfree\s+nft\b',
    r'\bairdrop\b.*\bclick\b',
    r'\bwhatsapp\.com\b',
    r't\.me/\+',                        # Telegram invite links
]

NON_TWITTER_LINK_RE = re.compile(
    r'https?://(?!(?:twitter\.com|x\.com|t\.co)/)[\w/.-]+',
    re.IGNORECASE
)

TWITTER_LINK_RE = re.compile(
    r'https?://(?:twitter\.com|x\.com)/\S+/status/(\d+)',
    re.IGNORECASE
)

WARN_STATE: dict[int, int] = {}  # user_id -> warn_count


def analyze_twitter_for_raid(tweet_url: str) -> Optional[dict]:
    """Fetch tweet content and analyze vibe with Claude."""
    try:
        # Fetch oEmbed (sync version)
        import requests
        oembed_url = f"https://publish.twitter.com/oembed?url={tweet_url}&maxwidth=550&omit_script=true"
        resp = requests.get(oembed_url, timeout=5)
        resp.raise_for_status()
        oembed_data = resp.json()

        tweet_html = oembed_data.get('html', '')
        # Extract text from blockquote (rough: strip HTML tags)
        tweet_text = re.sub('<[^<]+?>', '', tweet_html)

        # Get tweet ID
        match = TWITTER_LINK_RE.search(tweet_url)
        tweet_id = match.group(1) if match else None

        # Claude vibe analysis (Haiku tier, cheap)
        from .config import settings
        from .claude_client import ClaudeClient

        try:
            client = ClaudeClient()
            vibe_json = client.complete_json(
                system="You are a social media engagement analyst for crypto communities. Return ONLY JSON.",
                user=f"""Tag this tweet for a crypto raid group. Return JSON: {{"vibe_line": str, "tags": list[str]}}
vibe_line is 1 emoji + 4 words max.
tags are 3-6 from: hype, bullish, bearish, meme, pepe, wojak, degen, moon, wagmi, ngmi, based, chaos, chill, alpha, fud, energy_high, energy_low

Tweet: {tweet_text[:200]}""",
                model=settings.vibe_model,
                temperature=0.7,
            )
        except Exception:
            # Fallback if API key not set
            vibe_json = {'vibe_line': '?', 'tags': []}

        vibe_line = vibe_json.get('vibe_line', '?')
        tags = vibe_json.get('tags', [])

        # Search gallery for matching images
        gallery_items = search_by_vibe(tags, limit=4)

        return {
            'tweet_url': tweet_url,
            'tweet_id': tweet_id,
            'tweet_text': tweet_text[:100],
            'vibe_line': vibe_line,
            'tags': tags,
            'gallery_items': gallery_items,
        }
    except Exception as e:
        return None


def is_scam(text: str) -> bool:
    """Check if message matches scam patterns."""
    text_lower = text.lower()
    for pattern in SCAM_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def has_slur(text: str, banned_words: list[str]) -> bool:
    """Check if message contains banned words."""
    text_lower = text.lower()
    for word in banned_words:
        if re.search(r'\b' + re.escape(word) + r'\b', text_lower):
            return True
    return False


def has_non_twitter_link(text: str) -> bool:
    """Check if text contains non-Twitter URLs."""
    return NON_TWITTER_LINK_RE.search(text) is not None


def extract_twitter_url(text: str) -> Optional[str]:
    """Extract Twitter URL from text."""
    match = TWITTER_LINK_RE.search(text)
    if match:
        # Reconstruct full URL
        start = match.start()
        end = match.end()
        return text[start:end]
    return None


# ---------------------------------------------------------------------------
# Live bot runner — only imports python-telegram-bot when actually run.
# ---------------------------------------------------------------------------


def run() -> None:  # pragma: no cover — requires network + credentials
    """Start the Telegram bot in polling mode.

    Requires env vars:
      TELEGRAM_BOT_TOKEN
      MEMEGINE_TELEGRAM_OPERATOR_CHAT_ID
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    operator_chat_id = os.environ.get("MEMEGINE_TELEGRAM_OPERATOR_CHAT_ID")
    if not token or not operator_chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and MEMEGINE_TELEGRAM_OPERATOR_CHAT_ID must be set"
        )

    from telegram import Update
    from telegram.constants import ParseMode
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    operator = OperatorOnly(int(operator_chat_id))

    async def _reply(update: Update, messages: list[str]) -> None:
        for msg in messages:
            await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def guard(update: Update) -> bool:
        if not operator(update):
            await update.effective_message.reply_text("not authorized")
            return False
        return True

    async def cmd_piece(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        args = " ".join(context.args or [])
        intent, kind, fmt = parse_piece_args(args)
        if not intent:
            await _reply(update, ["usage: /piece <intent> [image|video] [-f <format>]"])
            return
        await _reply(update, handle_piece(intent, kind, fmt))

    async def cmd_brief(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        intent, fmt = parse_brief_args(" ".join(context.args or []))
        if not intent:
            await _reply(update, ["usage: /brief <intent> [-f <format>]"])
            return
        await _reply(update, handle_brief(intent, fmt))

    async def cmd_shots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        intent = " ".join(context.args or [])
        if not intent:
            await _reply(update, ["usage: /shots <intent>"])
            return
        await _reply(update, handle_shots(intent))

    async def cmd_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        concept = " ".join(context.args or [])
        if not concept:
            await _reply(update, ["usage: /caption <concept>"])
            return
        await _reply(update, handle_caption(concept))

    async def cmd_capture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        intent = " ".join(context.args or [])
        if not intent:
            await _reply(update, ["usage: /capture <rough thought>"])
            return
        await _reply(update, handle_capture(intent))

    async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        await _reply(update, handle_queue())

    async def cmd_formats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        await _reply(update, handle_formats())

    async def cmd_codex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        await _reply(update, handle_codex())

    async def cmd_get_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        await _reply(update, handle_get_group_id(chat_id))

    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        await _reply(update, [
            "*Memegine commands*\n"
            "/piece <intent> [image|video] [-f fmt]\n"
            "/brief <intent> [-f fmt]\n"
            "/shots <intent>\n"
            "/caption <concept>\n"
            "/capture <rough thought>\n"
            "/queue\n"
            "/formats\n"
            "/codex\n"
        ])

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("piece", cmd_piece))
    application.add_handler(CommandHandler("brief", cmd_brief))
    application.add_handler(CommandHandler("shots", cmd_shots))
    application.add_handler(CommandHandler("caption", cmd_caption))
    application.add_handler(CommandHandler("capture", cmd_capture))
    application.add_handler(CommandHandler("queue", cmd_queue))
    application.add_handler(CommandHandler("formats", cmd_formats))
    application.add_handler(CommandHandler("codex", cmd_codex))
    application.add_handler(CommandHandler("get_group_id", cmd_get_group_id))
    application.add_handler(CommandHandler(["help", "start"], cmd_help))

    # Group moderation and raid coordination
    from .config import settings

    async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Main group message filter: moderation + Twitter analysis."""
        if not update.effective_message or not update.effective_chat:
            return

        # Only handle the raid group
        if settings.raid_group_id and update.effective_chat.id != settings.raid_group_id:
            return

        message = update.effective_message
        text = message.text or ""
        user_id = message.from_user.id if message.from_user else None

        # 1. SCAM CHECK - instant ban + delete
        if is_scam(text):
            try:
                await message.delete()
                if user_id:
                    await context.bot.ban_chat_member(update.effective_chat.id, user_id)
                await context.bot.send_message(
                    update.effective_chat.id,
                    f"🚫 @{message.from_user.username or 'user'} has been banned for scam"
                )
            except Exception:
                pass
            return

        # 2. SLUR CHECK - delete + warn (1st) or ban (repeat)
        if text and settings.banned_words and has_slur(text, settings.banned_words):
            try:
                await message.delete()
                warns = WARN_STATE.get(user_id, 0) + 1
                WARN_STATE[user_id] = warns
                if warns >= 2:
                    await context.bot.ban_chat_member(update.effective_chat.id, user_id)
                    await context.bot.send_message(
                        update.effective_chat.id,
                        f"🚫 @{message.from_user.username or 'user'} has been banned"
                    )
                else:
                    await context.bot.send_message(
                        update.effective_chat.id,
                        f"⚠️ @{message.from_user.username or 'user'} warned ({warns}/2)"
                    )
            except Exception:
                pass
            return

        # 3. BAD LINK CHECK - delete non-Twitter URLs silently
        if text and has_non_twitter_link(text):
            try:
                await message.delete()
            except Exception:
                pass
            return

        # 4. TWITTER LINK CHECK - pin + analyze + post vibe
        twitter_url = extract_twitter_url(text) if text else None
        if twitter_url:
            try:
                # Pin the message
                await message.pin(disable_notification=True)

                # Analyze tweet
                raid_data = analyze_twitter_for_raid(twitter_url)
                if raid_data:
                    vibe = raid_data.get('vibe_line', '?')
                    gallery = raid_data.get('gallery_items', [])

                    # Build engagement buttons
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

                    if raid_data.get('tweet_id'):
                        tweet_id = raid_data['tweet_id']
                        buttons = [
                            [
                                InlineKeyboardButton("❤️ Like", url=f"https://twitter.com/intent/favorite?tweet_id={tweet_id}"),
                                InlineKeyboardButton("🔁 Retweet", url=f"https://twitter.com/intent/retweet?tweet_id={tweet_id}"),
                                InlineKeyboardButton("💬 Reply", url=f"https://twitter.com/intent/tweet?in_reply_to={tweet_id}"),
                            ]
                        ]
                        keyboard = InlineKeyboardMarkup(buttons)

                        # Send vibe with buttons
                        vibe_msg = f"{vibe}\n\nClick below to engage:"
                        await context.bot.send_message(
                            update.effective_chat.id,
                            vibe_msg,
                            reply_markup=keyboard
                        )

                        # Log that we posted a raid
                        username = message.from_user.username or f"user_{message.from_user.id}"
                        log_raider_action(
                            message.from_user.id,
                            username,
                            "raid_posted",
                            raid_data.get('tweet_url', '')
                        )
                    else:
                        vibe_msg = f"{vibe}\n\n(Could not parse tweet ID)"
                        await context.bot.send_message(update.effective_chat.id, vibe_msg)
            except Exception as e:
                pass  # Silent on error
            return

    if settings.raid_group_id:
        # Add group handler for Twitter link detection
        application.add_handler(
            MessageHandler(
                filters.Chat(settings.raid_group_id) & ~filters.COMMAND,
                group_message_handler
            )
        )

    application.run_polling(allowed_updates=Update.ALL_TYPES)


__all__ = [
    "OperatorOnly",
    "handle_piece",
    "handle_brief",
    "handle_shots",
    "handle_caption",
    "handle_capture",
    "handle_queue",
    "handle_formats",
    "handle_codex",
    "parse_piece_args",
    "parse_brief_args",
    "is_scam",
    "has_slur",
    "has_non_twitter_link",
    "extract_twitter_url",
    "analyze_twitter_for_raid",
    "run",
]
