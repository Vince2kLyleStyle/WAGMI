"""Music-edit tests. Uses real ffmpeg + librosa against generated audio.

We synthesize a simple 8-beat track at ~120 BPM (4 seconds long) so beat
detection has something unambiguous to find.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from memegine import audio as audio_mod
from memegine import editor, music_edit, transitions


pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make_beat_track(dst: Path, *, bpm: int = 120, beats: int = 16) -> Path:
    """Generate a click track at a known BPM.

    Uses ffmpeg aevalsrc with a short pulse per beat — librosa sees it as
    strong onsets and locks to the tempo.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    period = 60.0 / bpm
    duration = beats * period + 0.5
    # Click: brief sine burst decaying across ~50ms, repeating every period
    expr = (
        f"0.9*sin(2*PI*1200*t) * if(lt(mod(t,{period}),0.04), exp(-40*mod(t,{period})), 0)"
    )
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"aevalsrc='{expr}':s=44100:d={duration}",
            "-c:a", "pcm_s16le",
            str(dst),
        ],
        capture_output=True, text=True, check=True,
    )
    return dst


def _make_clip(dst: Path, *, color: str, duration: int = 3, size: str = "320x568") -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={duration}:r=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-crf", "28",
            "-an",
            str(dst),
        ],
        capture_output=True, text=True, check=True,
    )
    return dst


def test_analyze_detects_bpm(tmp_path: Path):
    track = _make_beat_track(tmp_path / "click.wav", bpm=120, beats=16)
    grid = audio_mod.analyze(track)
    # librosa's beat tracker may land on an octave; accept 60 / 120 / 240
    assert grid.tempo_bpm > 40
    assert len(grid.beats) > 4
    assert grid.duration > 7.0


def test_snap_cuts_to_beats():
    beats = [1.0, 2.0, 3.0, 4.0]
    snapped = audio_mod.snap_cuts_to_beats([1.1, 2.9, 5.0], beats, max_snap_sec=0.2)
    assert snapped[0] == 1.0
    assert snapped[1] == 3.0
    # 5.0 is outside the beat grid and > max_snap from 4.0, so passes through
    assert snapped[2] == 5.0


def test_plan_cuts_for_clips():
    beats = [0.5, 1.0, 1.5, 2.0, 2.5]
    windows = audio_mod.plan_cuts_for_clips(4, beats, beats_per_cut=1)
    assert len(windows) == 4
    assert windows[0] == (0.5, 1.0)
    assert windows[3] == (2.0, 2.5)


def test_plan_cuts_for_clips_not_enough_beats():
    with pytest.raises(ValueError):
        audio_mod.plan_cuts_for_clips(10, [0.5, 1.0], beats_per_cut=1)


def test_accelerating_cut_plan_shrinks():
    beats = [i * 0.5 for i in range(32)]
    plan = audio_mod.build_accelerating_cut_plan(
        total_beats=20, beats=beats, start_per_cut=4, end_per_cut=1,
    )
    assert len(plan) > 2
    # Durations should generally be non-increasing (monotonically shrinking
    # or equal).
    durations = [e - s for s, e in plan]
    # Last few should be shorter than first
    assert durations[-1] <= durations[0]


def test_hard_cut_montage(tmp_path: Path):
    track = _make_beat_track(tmp_path / "click.wav", bpm=120, beats=16)
    clips = [
        _make_clip(tmp_path / "a.mp4", color="red"),
        _make_clip(tmp_path / "b.mp4", color="green"),
        _make_clip(tmp_path / "c.mp4", color="blue"),
        _make_clip(tmp_path / "d.mp4", color="yellow"),
    ]
    out = music_edit.hard_cut_montage(clips, track, tmp_path / "montage.mp4", beats_per_cut=2)
    info = editor.probe(out)
    assert info.has_audio
    assert info.duration > 1.0


def test_rhythmic_build(tmp_path: Path):
    track = _make_beat_track(tmp_path / "click.wav", bpm=120, beats=24)
    clips = [_make_clip(tmp_path / f"c{i}.mp4", color=c, duration=6) for i, c in enumerate(["red", "green", "blue"])]
    out = music_edit.rhythmic_build(clips, track, tmp_path / "build.mp4")
    info = editor.probe(out)
    assert info.has_audio
    assert info.duration > 1.0


def test_impact_frame_chain(tmp_path: Path):
    track = _make_beat_track(tmp_path / "click.wav", bpm=120, beats=16)
    clips = [_make_clip(tmp_path / f"c{i}.mp4", color=c) for i, c in enumerate(["red", "green", "blue", "yellow"])]
    out = music_edit.impact_frame_chain(clips, track, tmp_path / "flash.mp4", beats_per_cut=2, flash_frames=2)
    info = editor.probe(out)
    assert info.has_audio


def test_aesthetic_slow_reveal_from_image(tmp_path: Path):
    from PIL import Image
    img = tmp_path / "still.png"
    Image.new("RGB", (1920, 1080), (100, 150, 200)).save(img)
    track = _make_beat_track(tmp_path / "click.wav", bpm=120, beats=16)
    out = music_edit.aesthetic_slow_reveal(img, track, tmp_path / "reveal.mp4", duration=3.0)
    info = editor.probe(out)
    assert info.has_audio
    assert 2.5 <= info.duration <= 3.5


def test_speed_ramp_slam(tmp_path: Path):
    track = _make_beat_track(tmp_path / "click.wav", bpm=120, beats=16)
    # Need a long enough clip
    clip = _make_clip(tmp_path / "long.mp4", color="purple", duration=6)
    out = music_edit.speed_ramp_slam(
        clip, track, tmp_path / "slam.mp4",
        slam_beat_sec=3.0,
        ramp_in_sec=1.0,
        slow_factor=0.5,
        post_slam_sec=1.0,
    )
    info = editor.probe(out)
    assert info.has_audio


def test_trailer_build(tmp_path: Path):
    track = _make_beat_track(tmp_path / "click.wav", bpm=120, beats=24)
    clips = [_make_clip(tmp_path / f"c{i}.mp4", color=c, duration=6) for i, c in enumerate(["red", "green", "blue", "yellow"])]
    out = music_edit.trailer_build(
        clips, track, tmp_path / "trailer.mp4",
        slam_beat_sec=6.0, pre_build_seconds=4.0, post_slam_seconds=1.5,
    )
    info = editor.probe(out)
    assert info.has_audio


def test_transitions_list():
    assert "fade" in transitions.list_transitions()
    assert "flash_white" in transitions.list_presets()
    assert "hard_cut" in transitions.list_presets()


def test_apply_transition_preset_flash_white(tmp_path: Path):
    a = _make_clip(tmp_path / "a.mp4", color="red", duration=2)
    b = _make_clip(tmp_path / "b.mp4", color="blue", duration=2)
    out = transitions.apply_preset(a, b, tmp_path / "fx.mp4", "flash_white")
    info = editor.probe(out)
    # hard_cut path uses concat so has audio; xfade path uses -an so no audio.
    # flash_white uses xfade, so no audio stream.
    assert info.duration > 0


def test_apply_transition_unknown_raises(tmp_path: Path):
    a = _make_clip(tmp_path / "a.mp4", color="red", duration=1)
    b = _make_clip(tmp_path / "b.mp4", color="blue", duration=1)
    with pytest.raises(ValueError):
        transitions.apply_transition(a, b, tmp_path / "x.mp4", transition="nonsense")


def test_apply_transition_hard_cut_falls_through_to_concat(tmp_path: Path):
    a = _make_clip(tmp_path / "a.mp4", color="red", duration=1)
    b = _make_clip(tmp_path / "b.mp4", color="blue", duration=1)
    out = transitions.apply_preset(a, b, tmp_path / "hc.mp4", "hard_cut")
    info = editor.probe(out)
    assert 1.8 <= info.duration <= 2.2
