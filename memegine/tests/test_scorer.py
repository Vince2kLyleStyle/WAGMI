from memegine.scorer import score


GREAT_IMAGE = (
    "A tired trader in his late 20s sitting at a desk at 3am, staring at a phone screen, "
    "shot on a Canon EOS R5 with 85mm f/1.2, Cinestill 800T look with subtle halation, "
    "single practical desk lamp as the only light source, overcast outside dark window, "
    "rule of thirds with subject left, grey hoodie, exhausted mood, photoreal, no CGI, "
    "natural skin pores visible, no extra fingers, no warped text, no plastic skin."
)


GREAT_MOTION = (
    "Slow push-in on the same trader, camera moves forward 15cm over the duration. "
    "5 seconds. 85mm f/1.2, Cinestill 800T, practical desk lamp, 3am overcast, "
    "rule of thirds subject left, subject holds still except for a slight exhale, "
    "lighting frozen, no wardrobe changes, no face morph, no cuts."
)


SLOP_IMAGE = "a cinematic stunning epic 4k portrait, beautiful, masterpiece"


def test_great_image_scores_high():
    r = score(GREAT_IMAGE, kind="image")
    assert r.score >= 85, r.as_text()
    assert "no banned superlatives" in r.strengths


def test_great_motion_scores_high():
    r = score(GREAT_MOTION, kind="motion")
    assert r.score >= 80, r.as_text()


def test_slop_image_scores_low():
    r = score(SLOP_IMAGE, kind="image")
    assert r.score <= 40, r.as_text()
    assert r.subscores["banned_words"] == 0


def test_missing_craft_loses_points():
    r = score("a man sitting", kind="image")
    assert r.subscores["craft_coverage"] < 20
    assert r.score < 60


def test_motion_without_camera_move_loses_5():
    r = score(
        "A trader at 3am, 85mm, Portra 400, window light, overcast, rule of thirds, exhausted, no extra fingers, no warped text",
        kind="motion",
    )
    assert r.subscores["camera_move"] == 0


def test_length_below_sweet_spot():
    r = score("man, 35mm, Portra, window, dusk, thirds, tired", kind="image")
    # Very terse — length should penalize
    assert r.subscores["length"] <= 6


def test_length_way_too_long():
    long_p = GREAT_IMAGE + " and " + ("more detail, " * 60)
    r = score(long_p, kind="image")
    assert r.subscores["length"] <= 6


def test_negative_list_detected():
    good = GREAT_IMAGE
    assert score(good).subscores["negative_list"] == 10
    without = (
        "tired trader at 3am, 85mm f/1.2, Cinestill 800T, window light, overcast, "
        "rule of thirds subject left, exhausted"
    )
    assert score(without).subscores["negative_list"] == 0


def test_as_text_renders():
    r = score(GREAT_IMAGE)
    text = r.as_text()
    assert "score:" in text
    assert "subscores:" in text
