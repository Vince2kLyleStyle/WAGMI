"""Audio analysis — beat detection, onset detection, tempo estimation,
and audio slicing to beat grids.

Uses librosa (free, BSD). Output is numeric arrays you can pass to editor
functions to produce cuts synced to music.

A typical workflow:
    beats = audio.beat_times("music.mp3")
    audio.fit_clips_to_beats(["a.mp4", "b.mp4", "c.mp4"], beats, "music.mp3", "out.mp4")
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class BeatGrid:
    """Result of beat analysis."""
    tempo_bpm: float
    beats: list[float]       # seconds, where each beat lands
    downbeats: list[float]   # estimated downbeats (every 4th beat by default)
    duration: float
    onset_envelope: np.ndarray  # strength of onset over time
    onset_times: np.ndarray     # frame times for onset_envelope

    @property
    def beat_intervals(self) -> list[float]:
        """Duration between consecutive beats."""
        if len(self.beats) < 2:
            return []
        return [b - a for a, b in zip(self.beats[:-1], self.beats[1:])]

    @property
    def avg_beat_interval(self) -> float:
        ivs = self.beat_intervals
        return float(np.mean(ivs)) if ivs else 0.0


def analyze(path: str | Path, *, hop_length: int = 512) -> BeatGrid:
    """Full beat + tempo analysis of an audio file."""
    import librosa  # lazy import: expensive
    y, sr = librosa.load(str(path), sr=None, mono=True)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop_length)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    onset_times = librosa.frames_to_time(np.arange(len(onset_env)), sr=sr, hop_length=hop_length)
    duration = float(len(y) / sr)

    tempo_val = float(np.atleast_1d(tempo)[0])
    beats_list = [float(t) for t in beat_times]
    # downbeats: every 4th beat starting from the first (approximation of 4/4)
    downbeats = beats_list[::4]

    return BeatGrid(
        tempo_bpm=tempo_val,
        beats=beats_list,
        downbeats=downbeats,
        duration=duration,
        onset_envelope=onset_env,
        onset_times=onset_times,
    )


def snap_cuts_to_beats(
    target_cut_times: list[float],
    beats: list[float],
    *,
    max_snap_sec: float = 0.2,
) -> list[float]:
    """Snap a list of desired cut times to the nearest beat within max_snap_sec.

    If no beat is within range, the original time is kept.
    """
    if not beats:
        return list(target_cut_times)
    bp = np.asarray(beats)
    out = []
    for t in target_cut_times:
        diffs = np.abs(bp - t)
        idx = int(np.argmin(diffs))
        if diffs[idx] <= max_snap_sec:
            out.append(float(bp[idx]))
        else:
            out.append(float(t))
    return out


def plan_cuts_for_clips(
    clip_count: int,
    beats: list[float],
    *,
    beats_per_cut: int = 1,
    start_beat: int = 0,
) -> list[tuple[float, float]]:
    """Given N clips and a beat grid, return the (start, end) windows in the
    *audio* timeline where each clip should play.

    Example: clip_count=4, beats=[0.5, 1.0, 1.5, 2.0, 2.5], beats_per_cut=1
    -> [(0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 2.5)]

    Raises ValueError if there aren't enough beats.
    """
    if clip_count <= 0:
        return []
    needed = clip_count * beats_per_cut + 1  # n cuts + 1 endpoint
    available = len(beats) - start_beat
    if available < needed:
        raise ValueError(
            f"not enough beats: need {needed} from index {start_beat}, have {available}"
        )
    windows: list[tuple[float, float]] = []
    for i in range(clip_count):
        start = beats[start_beat + i * beats_per_cut]
        end = beats[start_beat + (i + 1) * beats_per_cut]
        windows.append((float(start), float(end)))
    return windows


def build_accelerating_cut_plan(
    total_beats: int,
    beats: list[float],
    *,
    start_beat: int = 0,
    start_per_cut: int = 4,
    end_per_cut: int = 1,
) -> list[tuple[float, float]]:
    """Rhythmic build: cuts start long (e.g. 4 beats each) and accelerate to
    short (1 beat each) over the course of the section.

    Returns a list of (start, end) windows in audio time.
    """
    windows: list[tuple[float, float]] = []
    b = start_beat
    remaining = total_beats
    step = start_per_cut
    # We'll linearly decrease step from start_per_cut to end_per_cut
    # by computing how many cuts fit first.
    # Simpler approach: advance by a shrinking step each iteration.
    ratio = 0.85  # each cut shrinks 15%
    while remaining > 0 and b + step < len(beats):
        end_b = min(b + step, len(beats) - 1)
        if end_b <= b:
            break
        windows.append((float(beats[b]), float(beats[end_b])))
        remaining -= step
        b = end_b
        step = max(end_per_cut, int(step * ratio))
    return windows


def find_drop(
    grid: BeatGrid,
    *,
    search_start_sec: float = 5.0,
    window_sec: float = 4.0,
) -> float | None:
    """Estimate the "drop" — the beat after the biggest onset-energy spike.

    Returns the beat time nearest the peak, or None if the audio is too short.
    """
    if grid.duration < search_start_sec + window_sec:
        return None
    # Smooth onset envelope with a rolling mean to kill micro-spikes.
    env = grid.onset_envelope
    win = max(1, int(len(env) / grid.duration * 0.5))  # ~0.5s smoothing
    if win > 1:
        kernel = np.ones(win) / win
        smoothed = np.convolve(env, kernel, mode="same")
    else:
        smoothed = env

    # Mask to the search window
    mask = (grid.onset_times >= search_start_sec) & (
        grid.onset_times <= grid.duration - 1.0
    )
    if not mask.any():
        return None
    candidates = smoothed.copy()
    candidates[~mask] = -np.inf
    peak_idx = int(np.argmax(candidates))
    peak_time = float(grid.onset_times[peak_idx])
    # Snap to the nearest beat at or after the peak
    later = [b for b in grid.beats if b >= peak_time]
    if later:
        return float(later[0])
    return float(grid.beats[-1]) if grid.beats else None


__all__ = [
    "BeatGrid",
    "analyze",
    "snap_cuts_to_beats",
    "plan_cuts_for_clips",
    "build_accelerating_cut_plan",
    "find_drop",
]
