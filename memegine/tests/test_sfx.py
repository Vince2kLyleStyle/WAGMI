from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from memegine import sfx, editor


pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _probe_audio(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-print_format", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    import json
    return json.loads(out.stdout)


def test_whoosh_creates_audio_file(tmp_path: Path):
    out = sfx.whoosh(tmp_path / "whoosh.m4a", duration=0.3, direction="up")
    assert out.exists()
    meta = _probe_audio(out)
    assert any(s.get("codec_type") == "audio" for s in meta["streams"])


def test_impact_hard(tmp_path: Path):
    out = sfx.impact(tmp_path / "hit.m4a", intensity="hard", duration=0.4)
    assert out.exists()
    assert out.stat().st_size > 500


def test_impact_cinematic(tmp_path: Path):
    out = sfx.impact(tmp_path / "cin.m4a", intensity="cinematic")
    assert out.exists()


def test_riser_creates_file(tmp_path: Path):
    out = sfx.riser(tmp_path / "riser.m4a", duration=1.0)
    assert out.exists()


def test_click_track_has_expected_duration(tmp_path: Path):
    out = sfx.click_track(tmp_path / "click.m4a", bpm=120, beats=8)
    meta = _probe_audio(out)
    duration = float(meta["format"]["duration"])
    # 8 beats at 120 BPM = 4s; allow slack for fade and encoding
    assert 3.5 <= duration <= 4.6


def test_layer_sfx_onto_video(tmp_path: Path):
    # Make a 2s silent clip
    video = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:d=2:r=30",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t", "2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-crf", "28",
            "-c:a", "aac",
            str(video),
        ], capture_output=True, text=True, check=True,
    )

    whoosh_file = sfx.whoosh(tmp_path / "w.m4a", duration=0.25)
    impact_file = sfx.impact(tmp_path / "i.m4a", duration=0.3)

    out = sfx.layer_sfx(
        video, tmp_path / "layered.mp4",
        sfx_cues=[(whoosh_file, 0.5), (impact_file, 1.2)],
    )
    assert out.exists()
    info = editor.probe(out)
    assert info.has_audio
    assert info.duration >= 1.9
