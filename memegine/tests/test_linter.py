from memegine.linter import lint


GOOD_PORTRAIT = (
    "trader sitting at a desk at 3am, monitor glow on his face, "
    "Canon EOS R5 with 85mm f/1.2, Cinestill 800T look, "
    "hard directional practical light from right, rule of thirds subject left, "
    "no extra fingers, no warped text"
)

GOOD_MOTION = (
    "slow push-in on a trader at his desk at 3am, monitor glow on his face, "
    "35mm f/1.4, Cinestill 800T, hard side light, rule of thirds, "
    "subject holds still, no cuts"
)


def test_good_image_prompt_passes():
    r = lint(GOOD_PORTRAIT, kind="image")
    assert r.ok, r.as_text()
    assert not r.errors
    # craft coverage should be full
    assert all(r.hits.values()), r.hits


def test_good_motion_prompt_passes():
    r = lint(GOOD_MOTION, kind="motion")
    assert r.ok, r.as_text()


def test_banned_superlatives_trigger_errors():
    bad = "a cinematic, epic, stunning shot of a man, 4k"
    r = lint(bad, kind="image")
    assert not r.ok
    msgs = " ".join(i.message for i in r.errors)
    assert "cinematic" in msgs
    assert "epic" in msgs
    assert "stunning" in msgs
    assert "4k" in msgs


def test_motion_without_camera_move_errors():
    bad = "a photoreal scene of a trader, Portra 400, golden hour, rule of thirds"
    r = lint(bad, kind="motion")
    assert not r.ok
    assert any("camera move" in i.message for i in r.errors)


def test_warnings_for_missing_craft_cues():
    plain = "a man sitting somewhere"
    r = lint(plain, kind="image")
    # no banned words, so technically 'ok' but should have warnings
    assert r.ok
    assert len(r.warnings) >= 3
