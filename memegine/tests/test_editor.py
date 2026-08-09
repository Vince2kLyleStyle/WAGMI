"""FFmpeg editor tests. Use real ffmpeg generated fixtures (synthetic clips)
so we validate actual pipeline output, not mocks.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from memegine import editor


pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make_clip(dst: Path, *, color: str = "red", size: str = "640x1138", duration: int = 2, with_audio: bool = True) -> Path:
    """Generate a synthetic test clip via ffmpeg lavfi."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={duration}:r=30",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-crf", "28",
    ]
    if with_audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(dst)]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return dst


def _make_image(dst: Path, *, color: str = "blue", size: str = "640x640") -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}", "-frames:v", "1", str(dst)],
        capture_output=True, text=True, check=True,
    )
    return dst


def test_probe_returns_basics(tmp_path: Path):
    clip = _make_clip(tmp_path / "clip.mp4", duration=2)
    info = editor.probe(clip)
    assert info.width == 640
    assert info.height == 1138
    assert info.duration == pytest.approx(2.0, rel=0.1)
    assert info.has_audio is True
    assert 29 <= info.fps <= 31


def test_to_aspect_cover(tmp_path: Path):
    src = _make_clip(tmp_path / "src.mp4", size="1280x720", duration=1)
    out = editor.to_aspect(src, "9:16", tmp_path / "out.mp4", fit="cover")
    info = editor.probe(out)
    assert info.width == 1080
    assert info.height == 1920


def test_to_aspect_contain_pads(tmp_path: Path):
    src = _make_clip(tmp_path / "src.mp4", size="1280x720", duration=1)
    out = editor.to_aspect(src, "1:1", tmp_path / "out.mp4", fit="contain")
    info = editor.probe(out)
    assert info.width == 1080
    assert info.height == 1080


def test_ken_burns_from_image(tmp_path: Path):
    img = _make_image(tmp_path / "still.png", size="1920x1080")
    out = editor.ken_burns(img, tmp_path / "kb.mp4", duration=2.0, ratio="9:16")
    info = editor.probe(out)
    assert info.width == 1080
    assert info.height == 1920
    assert 1.8 <= info.duration <= 2.2


def test_concat_two_clips(tmp_path: Path):
    a = _make_clip(tmp_path / "a.mp4", color="red", duration=1)
    b = _make_clip(tmp_path / "b.mp4", color="blue", duration=1)
    out = editor.concat([a, b], tmp_path / "ab.mp4")
    info = editor.probe(out)
    assert 1.8 <= info.duration <= 2.3


def test_concat_handles_missing_audio(tmp_path: Path):
    a = _make_clip(tmp_path / "a.mp4", duration=1, with_audio=False)
    b = _make_clip(tmp_path / "b.mp4", duration=1, with_audio=True)
    out = editor.concat([a, b], tmp_path / "ab.mp4")
    info = editor.probe(out)
    assert info.has_audio is True


def test_crossfade_shortens_total_duration(tmp_path: Path):
    a = _make_clip(tmp_path / "a.mp4", duration=2, with_audio=False)
    b = _make_clip(tmp_path / "b.mp4", duration=2, with_audio=False)
    out = editor.crossfade(a, b, tmp_path / "xf.mp4", duration=0.5)
    info = editor.probe(out)
    # total = a_dur + b_dur - fade = 2 + 2 - 0.5 = 3.5
    assert 3.2 <= info.duration <= 3.8


def test_drawtext_burns_caption(tmp_path: Path):
    src = _make_clip(tmp_path / "src.mp4", duration=1)
    out = editor.drawtext(src, tmp_path / "cap.mp4", "hello world", position="bottom")
    assert out.exists()
    info = editor.probe(out)
    assert info.duration > 0


def test_speed_up_halves_duration(tmp_path: Path):
    src = _make_clip(tmp_path / "src.mp4", duration=2, with_audio=False)
    out = editor.speed(src, tmp_path / "fast.mp4", factor=2.0)
    info = editor.probe(out)
    assert 0.8 <= info.duration <= 1.2


def test_add_audio_replace(tmp_path: Path):
    src = _make_clip(tmp_path / "src.mp4", duration=2, with_audio=False)
    audio = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=2", str(audio)],
        capture_output=True, text=True, check=True,
    )
    out = editor.add_audio(src, audio, tmp_path / "withaudio.mp4", mode="replace")
    info = editor.probe(out)
    assert info.has_audio


def test_atempo_chain_extreme():
    assert editor._atempo_chain(4.0) == "atempo=2.0,atempo=2.0"
    assert editor._atempo_chain(0.25) == "atempo=0.5,atempo=0.5"
    assert editor._atempo_chain(1.5) == "atempo=1.5"
