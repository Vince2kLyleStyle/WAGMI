"""Music-synced edit templates.

High-level composable functions that take (clips, music, style) and produce
finished music-edit videos. Built on editor.py + transitions.py + audio.py.

Templates:
- hard_cut_montage    : N clips, 1 cut per beat, hard cuts only
- rhythmic_build      : cuts accelerate through the section
- speed_ramp_slam     : slow-mo leading into a named beat, snap on the beat
- impact_frame_chain  : white/black flashes between clips on beats
- aesthetic_slow_reveal : one clip, slow push, fits music duration
- trailer_build       : long cuts -> accelerating -> slam on the drop
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import audio as audio_mod
from . import editor, transitions


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    return exe


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {proc.returncode})\n"
            f"cmd: {' '.join(cmd)}\nstderr:\n{proc.stderr}"
        )


def _trim_and_normalize(
    src: str | Path,
    dst: str | Path,
    duration: float,
    *,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Trim src to exactly `duration` seconds, pad to width x height, set fps.
    If src is shorter than duration, it's looped.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    info = editor.probe(src)
    inputs = ["-i", str(src)]
    if info.duration < duration:
        # Use stream_loop for videos shorter than needed
        inputs = ["-stream_loop", "-1", "-i", str(src)]
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}"
    )
    _run([
        _ffmpeg(), "-y",
        *inputs,
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
        str(dst),
    ])
    return dst


def _mux_audio(
    video: str | Path,
    audio: str | Path,
    dst: str | Path,
    *,
    audio_start: float = 0.0,
    duration: float | None = None,
) -> Path:
    """Mux an audio track onto a silent video, optionally trimmed."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(), "-y",
        "-i", str(video),
        "-ss", f"{audio_start:.3f}", "-i", str(audio),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
    ]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    else:
        cmd += ["-shortest"]
    cmd += [str(dst)]
    _run(cmd)
    return dst


@dataclass
class EditPlan:
    """Plan for a music-edit piece. Returned by build_* functions; apply() renders."""
    template: str
    music_path: Path
    audio_start: float
    total_duration: float
    segments: list[dict]  # each: {src, start, end, ...template-specific fields}
    notes: str = ""


def hard_cut_montage(
    clips: list[str | Path],
    music: str | Path,
    dst: str | Path,
    *,
    beats_per_cut: int = 1,
    start_beat: int = 0,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Cut each clip on a beat. Hard cuts only. Music carried through.

    If there are more clips than beats, extras are dropped. If fewer clips
    than beats, the sequence loops through clips.
    """
    grid = audio_mod.analyze(music)
    # How many total cut-windows will fit?
    max_clips_fit = max(0, (len(grid.beats) - start_beat - 1) // beats_per_cut)
    n = min(len(clips), max_clips_fit)
    if n == 0:
        raise ValueError(
            f"not enough beats: only {len(grid.beats)} beats for beats_per_cut={beats_per_cut} from start_beat={start_beat}"
        )
    windows = audio_mod.plan_cuts_for_clips(n, grid.beats, beats_per_cut=beats_per_cut, start_beat=start_beat)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        normalized: list[Path] = []
        for i, ((a_start, a_end), clip) in enumerate(zip(windows, clips[:n])):
            dur = a_end - a_start
            out = _trim_and_normalize(clip, tmp_path / f"seg_{i:03d}.mp4", dur, width=width, height=height, fps=fps)
            normalized.append(out)

        silent = tmp_path / "silent.mp4"
        editor.concat(normalized, silent)

        audio_start = windows[0][0]
        final_duration = windows[-1][1] - audio_start
        return _mux_audio(silent, music, dst, audio_start=audio_start, duration=final_duration)


def rhythmic_build(
    clips: list[str | Path],
    music: str | Path,
    dst: str | Path,
    *,
    start_beat: int = 0,
    start_per_cut: int = 4,
    end_per_cut: int = 1,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Cuts start long (e.g. 4 beats each) and accelerate to 1-beat cuts."""
    grid = audio_mod.analyze(music)
    # How many beats available from start_beat to build over
    total_available = max(0, len(grid.beats) - start_beat - 1)
    windows = audio_mod.build_accelerating_cut_plan(
        total_available, grid.beats,
        start_beat=start_beat,
        start_per_cut=start_per_cut,
        end_per_cut=end_per_cut,
    )
    if not windows:
        raise ValueError("not enough beats for an accelerating build")

    # Cycle clips if there are more windows than clips
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        normalized: list[Path] = []
        for i, (a_start, a_end) in enumerate(windows):
            clip = clips[i % len(clips)]
            dur = a_end - a_start
            out = _trim_and_normalize(clip, tmp_path / f"seg_{i:03d}.mp4", dur, width=width, height=height, fps=fps)
            normalized.append(out)

        silent = tmp_path / "silent.mp4"
        editor.concat(normalized, silent)
        audio_start = windows[0][0]
        final_duration = windows[-1][1] - audio_start
        return _mux_audio(silent, music, dst, audio_start=audio_start, duration=final_duration)


def speed_ramp_slam(
    clip: str | Path,
    music: str | Path,
    dst: str | Path,
    *,
    slam_beat_sec: float | None = None,
    ramp_in_sec: float = 1.5,
    slow_factor: float = 0.4,
    post_slam_sec: float = 1.0,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Slow-mo leading into a beat, then snap to normal speed.

    If slam_beat_sec is None, the drop is auto-detected.
    """
    grid = audio_mod.analyze(music)
    slam = slam_beat_sec if slam_beat_sec is not None else audio_mod.find_drop(grid)
    if slam is None:
        raise ValueError("could not determine slam beat; pass slam_beat_sec")
    start_audio = max(0.0, slam - ramp_in_sec)
    end_audio = slam + post_slam_sec

    total_video_needed_real = ramp_in_sec * slow_factor + post_slam_sec

    info = editor.probe(clip)
    if info.duration < total_video_needed_real + 0.1:
        raise ValueError(
            f"clip too short ({info.duration:.2f}s) for slam plan needing {total_video_needed_real:.2f}s"
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Part 1: slow section. setpts=(1/slow_factor)*PTS slows playback.
        slow = tmp_path / "slow.mp4"
        slow_input_duration = ramp_in_sec * slow_factor  # real seconds of source
        _run([
            _ffmpeg(), "-y",
            "-i", str(clip),
            "-t", f"{slow_input_duration:.3f}",
            "-vf", f"setpts=(1/{slow_factor})*PTS,scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}",
            "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
            str(slow),
        ])

        # Part 2: normal-speed post-slam
        fast = tmp_path / "fast.mp4"
        _run([
            _ffmpeg(), "-y",
            "-ss", f"{slow_input_duration:.3f}",
            "-i", str(clip),
            "-t", f"{post_slam_sec:.3f}",
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}",
            "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
            str(fast),
        ])

        silent = tmp_path / "silent.mp4"
        editor.concat([slow, fast], silent)
        return _mux_audio(silent, music, dst, audio_start=start_audio, duration=(end_audio - start_audio))


def impact_frame_chain(
    clips: list[str | Path],
    music: str | Path,
    dst: str | Path,
    *,
    flash_color: str = "white",
    flash_frames: int = 2,
    beats_per_cut: int = 1,
    start_beat: int = 0,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Hard cuts between clips with a 2-frame flash on each beat transition."""
    grid = audio_mod.analyze(music)
    max_clips_fit = max(0, (len(grid.beats) - start_beat - 1) // beats_per_cut)
    n = min(len(clips), max_clips_fit)
    if n == 0:
        raise ValueError("not enough beats")
    windows = audio_mod.plan_cuts_for_clips(n, grid.beats, beats_per_cut=beats_per_cut, start_beat=start_beat)

    flash_duration = flash_frames / fps

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Generate a flash clip (solid color, flash_frames long)
        flash_src = tmp_path / "flash.mp4"
        _run([
            _ffmpeg(), "-y",
            "-f", "lavfi",
            "-i", f"color=c={flash_color}:s={width}x{height}:d={flash_duration:.3f}:r={fps}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
            "-an",
            str(flash_src),
        ])

        segments: list[Path] = []
        for i, ((a_start, a_end), clip) in enumerate(zip(windows, clips[:n])):
            dur = max(0.05, (a_end - a_start) - flash_duration)
            out = _trim_and_normalize(clip, tmp_path / f"seg_{i:03d}.mp4", dur, width=width, height=height, fps=fps)
            segments.append(out)
            if i < n - 1:
                segments.append(flash_src)

        silent = tmp_path / "silent.mp4"
        editor.concat(segments, silent)
        audio_start = windows[0][0]
        final_duration = windows[-1][1] - audio_start
        return _mux_audio(silent, music, dst, audio_start=audio_start, duration=final_duration)


def aesthetic_slow_reveal(
    clip: str | Path,
    music: str | Path,
    dst: str | Path,
    *,
    duration: float = 8.0,
    audio_start: float = 0.0,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    zoom_start: float = 1.0,
    zoom_end: float = 1.08,
) -> Path:
    """One clip, slow push-in, music underneath. The "vibe" template."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # If the clip is a still image, use Ken Burns; if it's video, just scale+zoom
        is_image = Path(clip).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        silent = tmp_path / "silent.mp4"
        if is_image:
            editor.ken_burns(
                clip, silent,
                duration=duration, ratio="9:16",
                zoom_start=zoom_start, zoom_end=zoom_end, fps=fps,
            )
        else:
            info = editor.probe(clip)
            if info.duration < duration + 0.1:
                # loop
                _run([
                    _ffmpeg(), "-y",
                    "-stream_loop", "-1", "-i", str(clip),
                    "-t", f"{duration:.3f}",
                    "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                           f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}",
                    "-an",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
                    str(silent),
                ])
            else:
                _run([
                    _ffmpeg(), "-y",
                    "-i", str(clip),
                    "-t", f"{duration:.3f}",
                    "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                           f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}",
                    "-an",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
                    str(silent),
                ])
        return _mux_audio(silent, music, dst, audio_start=audio_start, duration=duration)


def trailer_build(
    clips: list[str | Path],
    music: str | Path,
    dst: str | Path,
    *,
    slam_beat_sec: float | None = None,
    pre_build_seconds: float = 6.0,
    post_slam_seconds: float = 2.0,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Long cuts -> accelerating -> slam on the drop -> one held hero shot.

    Needs at least 3 clips. Drop is auto-detected if slam_beat_sec is None.
    """
    if len(clips) < 3:
        raise ValueError("trailer_build needs at least 3 clips")
    grid = audio_mod.analyze(music)
    slam = slam_beat_sec if slam_beat_sec is not None else audio_mod.find_drop(grid)
    if slam is None:
        raise ValueError("could not auto-detect drop; pass slam_beat_sec")

    # Pick a start beat such that pre_build_seconds fits before the slam
    build_start_time = max(0.0, slam - pre_build_seconds)
    start_beat = next((i for i, t in enumerate(grid.beats) if t >= build_start_time), 0)

    # Build section: use accelerating cuts
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Collect beat windows until we reach (or just pass) the slam
        windows: list[tuple[float, float]] = []
        b = start_beat
        step = 4
        while b + step < len(grid.beats) and grid.beats[b] < slam:
            end_b = b + step
            if grid.beats[end_b] > slam:
                # Snap last window to slam
                windows.append((grid.beats[b], slam))
                break
            windows.append((grid.beats[b], grid.beats[end_b]))
            b = end_b
            step = max(1, int(step * 0.6))
        if not windows:
            raise ValueError("could not build pre-drop section")

        normalized: list[Path] = []
        # use clips[:-1] cycled for the build, clips[-1] for the hero
        build_clips = clips[:-1]
        for i, (a_s, a_e) in enumerate(windows):
            clip = build_clips[i % len(build_clips)]
            dur = a_e - a_s
            out = _trim_and_normalize(clip, tmp_path / f"build_{i:03d}.mp4", dur, width=width, height=height, fps=fps)
            normalized.append(out)

        # Hero shot held for post_slam_seconds
        hero = _trim_and_normalize(
            clips[-1], tmp_path / "hero.mp4", post_slam_seconds,
            width=width, height=height, fps=fps,
        )
        normalized.append(hero)

        silent = tmp_path / "silent.mp4"
        editor.concat(normalized, silent)
        audio_start = windows[0][0]
        final_duration = (slam + post_slam_seconds) - audio_start
        return _mux_audio(silent, music, dst, audio_start=audio_start, duration=final_duration)


__all__ = [
    "EditPlan",
    "hard_cut_montage",
    "rhythmic_build",
    "speed_ramp_slam",
    "impact_frame_chain",
    "aesthetic_slow_reveal",
    "trailer_build",
]
