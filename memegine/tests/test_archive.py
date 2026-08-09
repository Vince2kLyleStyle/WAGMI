from pathlib import Path

from memegine import archive


def test_save_and_read_roundtrip(tmp_path: Path):
    b = archive.save(
        kind="prompt",
        intent="kilroy dunking on the fed",
        system="SYSTEM BLOCK",
        user="USER BLOCK",
        format_="meme_two_panel",
        logs_dir=tmp_path,
    )
    assert b.id
    assert b.kind == "prompt"
    recent = archive.read_recent(10, logs_dir=tmp_path)
    assert len(recent) == 1
    assert recent[0]["intent"] == "kilroy dunking on the fed"
    assert recent[0]["format"] == "meme_two_panel"


def test_find_by_id(tmp_path: Path):
    b1 = archive.save(kind="prompt", intent="a", system="s", user="u", logs_dir=tmp_path)
    b2 = archive.save(kind="shots", intent="b", system="s", user="u", logs_dir=tmp_path)
    hit = archive.find(b2.id, logs_dir=tmp_path)
    assert hit is not None
    assert hit["kind"] == "shots"
    assert archive.find("does-not-exist", logs_dir=tmp_path) is None


def test_search_text(tmp_path: Path):
    archive.save(kind="prompt", intent="trader at 3am", system="s", user="u1", logs_dir=tmp_path)
    archive.save(kind="prompt", intent="kilroy on a rooftop", system="s", user="u2", logs_dir=tmp_path)
    hits = archive.search("rooftop", logs_dir=tmp_path)
    assert len(hits) == 1
    assert "kilroy" in hits[0]["intent"]


def test_multiple_briefs_on_same_day(tmp_path: Path):
    for i in range(5):
        archive.save(kind="prompt", intent=f"brief {i}", system="s", user="u", logs_dir=tmp_path)
    recent = archive.read_recent(10, logs_dir=tmp_path)
    assert len(recent) == 5
    assert recent[0]["intent"] == "brief 4"  # newest first
