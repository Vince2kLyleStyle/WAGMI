from pathlib import Path

from memegine import pipeline, variants, reverse_engineer


def test_pipeline_image_creates_folder_and_files(tmp_path: Path):
    b = pipeline.build(
        "trader at 3am, market dumping, phone glow",
        kind="image",
        format_slug="photoreal_portrait",
        outputs_dir=tmp_path,
    )
    folder = Path(b.folder)
    assert folder.exists()
    assert (folder / "01-prompt.md").exists()
    assert (folder / "02-copy.md").exists()
    assert (folder / "README.md").exists()
    assert (folder / "bundle.json").exists()


def test_pipeline_video_uses_shot_list(tmp_path: Path):
    b = pipeline.build(
        "slow push-in on a rooftop at dusk, skyline goes dark",
        kind="video",
        outputs_dir=tmp_path,
    )
    folder = Path(b.folder)
    assert (folder / "01-shots.md").exists()
    assert (folder / "02-copy.md").exists()
    assert "shots" in b.briefs
    assert "prompt" not in b.briefs


def test_pipeline_skip_copy(tmp_path: Path):
    b = pipeline.build(
        "cope chart showing number line going up then crater",
        kind="image",
        format_slug="cope_chart",
        include_copy=False,
        outputs_dir=tmp_path,
    )
    assert "copy" not in b.briefs


def test_pipeline_bad_kind_raises(tmp_path: Path):
    import pytest
    with pytest.raises(ValueError):
        pipeline.build("x", kind="audio", outputs_dir=tmp_path)


def test_pipeline_image_requires_format(tmp_path: Path):
    import pytest
    with pytest.raises(ValueError):
        pipeline.build("x", kind="image", outputs_dir=tmp_path)


def test_variant_brief_structure():
    vb = variants.build_variant_brief(
        "35mm f/1.4 Portra 400 window light trader at 3am rule of thirds",
        n_variants=4,
    )
    assert "Seed prompt" in vb.user
    assert "TIME_OF_DAY" in vb.system or "TIME_OF_DAY" in vb.user


def test_reverse_brief_includes_path_and_rules():
    sys, user = reverse_engineer.build_reverse_brief("/tmp/fake.png", context="for a trader portrait")
    assert "recreate_prompt" in sys
    assert "/tmp/fake.png" in user
    assert "for a trader portrait" in user
