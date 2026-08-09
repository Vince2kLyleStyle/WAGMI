from pathlib import Path

from PIL import Image

from memegine import image_ops


def _make_png(path: Path, size=(1920, 1080), color=(200, 80, 50)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def test_probe(tmp_path: Path):
    p = _make_png(tmp_path / "a.png", size=(800, 600))
    info = image_ops.probe(p)
    assert info.width == 800
    assert info.height == 600


def test_to_aspect_cover(tmp_path: Path):
    src = _make_png(tmp_path / "wide.png", size=(1920, 1080))
    out = image_ops.to_aspect(src, "9:16", tmp_path / "out.png", fit="cover")
    info = image_ops.probe(out)
    assert info.width == 1080
    assert info.height == 1920


def test_to_aspect_contain_pads(tmp_path: Path):
    src = _make_png(tmp_path / "wide.png", size=(1920, 1080))
    out = image_ops.to_aspect(src, "1:1", tmp_path / "out.png", fit="contain", pad_color="black")
    info = image_ops.probe(out)
    assert info.width == 1080 and info.height == 1080


def test_caption_draws_text(tmp_path: Path):
    src = _make_png(tmp_path / "bg.png", size=(1080, 1920))
    out = image_ops.caption(src, tmp_path / "capped.png", "hello world", position="bottom")
    info = image_ops.probe(out)
    assert (info.width, info.height) == (1080, 1920)
    # Confirm pixel content changed (font was applied)
    before = Image.open(src).tobytes()
    after = Image.open(out).tobytes()
    assert before != after


def test_grid_pack(tmp_path: Path):
    ims = [_make_png(tmp_path / f"i{i}.png", size=(800, 800), color=(50 + i * 40, 100, 150)) for i in range(4)]
    out = image_ops.grid(ims, tmp_path / "grid.png", cols=2, cell=(400, 400), gap=4)
    info = image_ops.probe(out)
    assert info.width == 400 * 2 + 4
    assert info.height == 400 * 2 + 4


def test_two_panel_stack(tmp_path: Path):
    a = _make_png(tmp_path / "a.png", size=(1080, 1080), color=(200, 50, 50))
    b = _make_png(tmp_path / "b.png", size=(1080, 1080), color=(50, 50, 200))
    out = image_ops.two_panel(a, b, tmp_path / "out.png", ratio="4:5")
    info = image_ops.probe(out)
    assert (info.width, info.height) == (1080, 1350)


def test_blur_background_portrait(tmp_path: Path):
    src = _make_png(tmp_path / "wide.png", size=(1920, 1080))
    out = image_ops.blur_background_portrait(src, tmp_path / "portrait.png", ratio="9:16", blur=20)
    info = image_ops.probe(out)
    assert (info.width, info.height) == (1080, 1920)


def test_bad_ratio_raises(tmp_path: Path):
    import pytest
    src = _make_png(tmp_path / "a.png")
    with pytest.raises(ValueError):
        image_ops.to_aspect(src, "21:9", tmp_path / "out.png")  # type: ignore[arg-type]
