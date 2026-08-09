from pathlib import Path

from memegine import prompt_engine


def test_playbooks_exist():
    base = Path(__file__).resolve().parents[1] / "data" / "playbooks"
    required = ["grok-imagine-patterns", "video-img2vid-patterns", "meme-typography", "x-content-playbook", "music-edit-patterns"]
    for name in required:
        p = base / f"{name}.md"
        assert p.exists(), f"missing playbook: {p}"
        assert p.stat().st_size > 1000, f"playbook suspiciously small: {p}"


def test_load_playbook_returns_content():
    txt = prompt_engine.load_playbook("grok-imagine-patterns")
    assert "Grok" in txt
    assert "NEVER use" in txt or "Banned words" in txt or "banned" in txt.lower()


def test_load_playbook_missing_returns_empty():
    txt = prompt_engine.load_playbook("does-not-exist")
    assert txt == ""


def test_image_prompt_includes_image_playbooks():
    system, user = prompt_engine.assemble_offline_prompt(
        "trader at 3am, phone glow",
        format_slug="photoreal_portrait",
    )
    assert "grok-imagine-patterns" in user
    assert "meme-typography" in user
    # video-specific playbook should NOT be included for image formats
    assert "video-img2vid-patterns" not in user


def test_video_prompt_includes_video_playbook():
    system, user = prompt_engine.assemble_offline_prompt(
        "slow push-in on a trader's face",
        format_slug="photoreal_scene_motion",
    )
    assert "grok-imagine-patterns" in user
    assert "video-img2vid-patterns" in user
    # music-edit is now bundled with video briefs too
    assert "music-edit-patterns" in user


def test_prompt_can_exclude_playbooks():
    system, user = prompt_engine.assemble_offline_prompt(
        "anything",
        format_slug="photoreal_portrait",
        include_playbooks=False,
    )
    assert "grok-imagine-patterns" not in user
