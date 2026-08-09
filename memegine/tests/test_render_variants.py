from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from memegine import render_variants


pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _mk_image(dst: Path, size=(1920, 1080)) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (100, 150, 200)).save(dst)
    return dst


def _mk_clip(dst: Path, duration: int = 1) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x606080:s=640x640:d={duration}:r=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-crf", "28",
            str(dst),
        ], capture_output=True, text=True, check=True,
    )
    return dst


def test_render_aspect_variants_image(tmp_path: Path):
    src = _mk_image(tmp_path / "src.png", size=(1920, 1080))
    outs = render_variants.render_aspect_variants(src, tmp_path / "out", ratios=("9:16", "1:1"))
    assert len(outs) == 2
    assert all(p.exists() for p in outs)


def test_render_grade_variants_image(tmp_path: Path):
    src = _mk_image(tmp_path / "src.png", size=(400, 400))
    outs = render_variants.render_grade_variants(src, tmp_path / "out", presets=("portra_400", "moody_film"))
    assert len(outs) == 2
    assert all(p.exists() for p in outs)


def test_render_matrix_image(tmp_path: Path):
    src = _mk_image(tmp_path / "src.png", size=(1920, 1080))
    outs = render_variants.render_matrix(
        src, tmp_path / "out",
        ratios=("9:16", "1:1"),
        presets=("portra_400", "moody_film"),
    )
    assert len(outs) == 4
    # All names include both the aspect tag and the preset name
    names = {p.name for p in outs}
    assert any("9x16_portra_400" in n for n in names)
    assert any("1x1_moody_film" in n for n in names)


def test_render_matrix_video(tmp_path: Path):
    src = _mk_clip(tmp_path / "src.mp4")
    outs = render_variants.render_matrix(
        src, tmp_path / "out",
        ratios=("9:16",),
        presets=("teal_orange",),
    )
    assert len(outs) == 1
    assert outs[0].exists()
