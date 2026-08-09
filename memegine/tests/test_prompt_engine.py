from pathlib import Path

from memegine import prompt_engine, shot_list, copy_writer, style_codex


def test_formats_load():
    formats = prompt_engine.load_formats()
    slugs = {f.slug for f in formats}
    assert "photoreal_portrait" in slugs
    assert "meme_two_panel" in slugs
    assert "photoreal_scene_motion" in slugs


def test_assemble_offline_prompt_contains_intent_and_scaffold():
    system, user = prompt_engine.assemble_offline_prompt(
        "kilroy dunking on the fed", format_slug="meme_two_panel"
    )
    assert "Director" in system
    assert "cinematic" not in system.split("NEVER")[0]  # the word only appears in the 'forbidden' rule
    assert "kilroy dunking on the fed" in user
    assert "meme_two_panel" in user


def test_assemble_unknown_format_raises():
    import pytest

    with pytest.raises(ValueError):
        prompt_engine.assemble_offline_prompt("whatever", format_slug="does_not_exist")


def test_shot_list_user_message():
    system, user = shot_list.assemble_offline_shot_list_prompt(
        "slow push on a phone screen as a chart craters"
    )
    assert "shot list" in system.lower()
    assert "push" in user.lower()


def test_copy_writer_rules_present():
    system, user = copy_writer.assemble_offline_copy_prompt(
        "photoreal night portrait of a trader", asset_kind="image"
    )
    assert "NO emojis" in system
    assert "hashtag" in system.lower()
    assert "trader" in user


def test_codex_append_winner_roundtrip(tmp_path: Path):
    codex = tmp_path / "style.md"
    codex.write_text("# Codex\n\n## Proven Prompt Patterns\n- (empty)\n\n## Kill List\n- (empty)\n")
    style_codex.log_winner("85mm kodak portra 400 window light", "eyes sharp, skin natural", path=codex)
    text = codex.read_text()
    assert "85mm kodak portra 400" in text
    # section boundary respected: entry should be between header and next header
    assert text.index("85mm kodak") < text.index("## Kill List")
