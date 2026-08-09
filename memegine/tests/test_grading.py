"""Grading preset tests against real ffmpeg."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from memegine import grading


pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make_clip(dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=0x808080:s=320x240:d=1:r=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-crf", "28",
            str(dst),
        ],
        capture_output=True, text=True, check=True,
    )
    return dst


def _make_image(dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 200), (128, 128, 128)).save(dst)
    return dst


def test_list_presets_includes_core():
    names = set(grading.list_presets())
    for key in ("cinestill_800t", "portra_400", "tri_x_bw", "teal_orange", "moody_film"):
        assert key in names


def test_unknown_preset_raises(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        grading.apply_preset(tmp_path / "x.png", tmp_path / "o.png", "not_a_preset")


def test_apply_preset_on_still(tmp_path: Path):
    img = _make_image(tmp_path / "in.png")
    out = grading.apply_preset(img, tmp_path / "out.png", "teal_orange")
    assert out.exists()
    before = Image.open(img).tobytes()
    after = Image.open(out).tobytes()
    assert before != after  # actually changed pixels


def test_apply_preset_on_video(tmp_path: Path):
    clip = _make_clip(tmp_path / "in.mp4")
    out = grading.apply_preset(clip, tmp_path / "out.mp4", "portra_400")
    assert out.exists()
    assert out.stat().st_size > 0


@pytest.mark.parametrize("preset", sorted(grading.PRESETS.keys()))
def test_every_preset_applies_cleanly(tmp_path: Path, preset: str):
    """Every preset should successfully apply to a still without ffmpeg errors."""
    img = _make_image(tmp_path / "in.png")
    out = grading.apply_preset(img, tmp_path / f"{preset}.png", preset)
    assert out.exists()
