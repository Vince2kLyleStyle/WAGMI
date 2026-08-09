"""Tests for the idea queue (capture.py) and the Telegram bot handlers
(telegram_bot.py). Telegram tests are pure — they exercise the command
handlers directly, not the actual Telegram client.
"""
from __future__ import annotations

from pathlib import Path

from memegine import capture, telegram_bot


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def test_capture_add_and_list(tmp_path: Path):
    c1 = capture.add("trader at 3am", logs_dir=tmp_path)
    c2 = capture.add("kilroy meme about fed", logs_dir=tmp_path)
    pending = capture.list_pending(logs_dir=tmp_path)
    assert len(pending) == 2
    ids = {p.id for p in pending}
    assert c1.id in ids
    assert c2.id in ids


def test_capture_mark_consumed(tmp_path: Path):
    c = capture.add("rough idea", logs_dir=tmp_path)
    res = capture.mark_consumed(c.id, brief_id="brief-abc", logs_dir=tmp_path)
    assert res is not None
    assert res.status == "consumed"
    assert res.consumed_into_brief_id == "brief-abc"
    assert capture.list_pending(logs_dir=tmp_path) == []


def test_capture_discard(tmp_path: Path):
    c = capture.add("doesn't fit anywhere", logs_dir=tmp_path)
    res = capture.discard(c.id, logs_dir=tmp_path)
    assert res is not None
    assert res.status == "discarded"
    assert capture.list_pending(logs_dir=tmp_path) == []


def test_capture_find_by_prefix(tmp_path: Path):
    c = capture.add("idea", logs_dir=tmp_path)
    found = capture.find(c.id[:4], logs_dir=tmp_path)
    assert found is not None
    assert found.id == c.id


def test_capture_find_missing(tmp_path: Path):
    assert capture.find("zzz", logs_dir=tmp_path) is None


# ---------------------------------------------------------------------------
# telegram command handlers (pure logic)
# ---------------------------------------------------------------------------


def test_parse_piece_args_defaults():
    intent, kind, fmt = telegram_bot.parse_piece_args("trader at 3am")
    assert intent == "trader at 3am"
    assert kind == "image"
    assert fmt is None


def test_parse_piece_args_video_flag():
    intent, kind, fmt = telegram_bot.parse_piece_args("slow push video")
    assert intent == "slow push"
    assert kind == "video"


def test_parse_piece_args_format_flag():
    intent, kind, fmt = telegram_bot.parse_piece_args("kilroy dunking -f meme_two_panel")
    assert intent == "kilroy dunking"
    assert fmt == "meme_two_panel"


def test_parse_brief_args_defaults():
    intent, fmt = telegram_bot.parse_brief_args("night portrait")
    assert intent == "night portrait"
    assert fmt == "photoreal_portrait"


def test_parse_brief_args_format():
    intent, fmt = telegram_bot.parse_brief_args("fake news -f fake_news_headline")
    assert intent == "fake news"
    assert fmt == "fake_news_headline"


def test_handle_formats_returns_known_slugs():
    out = telegram_bot.handle_formats()
    combined = "\n".join(out)
    assert "photoreal_portrait" in combined
    assert "meme_two_panel" in combined


def test_handle_brief_returns_chunks():
    out = telegram_bot.handle_brief("trader at 3am", "photoreal_portrait")
    assert len(out) >= 2
    assert any("SYSTEM" in m for m in out)
    assert any("USER" in m for m in out)


def test_handle_brief_bad_format_returns_error():
    out = telegram_bot.handle_brief("anything", "does_not_exist")
    assert len(out) == 1
    assert out[0].startswith("❌")


def test_handle_shots_returns_brief():
    out = telegram_bot.handle_shots("10s push on a face")
    combined = "\n".join(out)
    assert "Shot list" in combined or "shot" in combined.lower()


def test_handle_caption_returns_brief():
    out = telegram_bot.handle_caption("night portrait of a trader")
    combined = "\n".join(out)
    assert "Caption" in combined or "caption" in combined.lower()


def test_handle_capture_and_queue(tmp_path: Path, monkeypatch):
    # Redirect capture's logs dir by patching settings at module level
    from memegine import config as cfg
    monkeypatch.setattr(cfg.settings, "logs_dir", tmp_path)
    out = telegram_bot.handle_capture("first idea")
    assert out[0].startswith("✓")
    out2 = telegram_bot.handle_queue()
    combined = "\n".join(out2)
    assert "first idea" in combined


def test_operator_only_guard():
    guard = telegram_bot.OperatorOnly(operator_chat_id=12345)
    class FakeChat:
        def __init__(self, id): self.id = id
    class FakeUpdate:
        def __init__(self, cid): self.effective_chat = FakeChat(cid)
    assert guard(FakeUpdate(12345)) is True
    assert guard(FakeUpdate(99999)) is False
