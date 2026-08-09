"""Transition library — FFmpeg xfade variants tuned for music-edit pacing.

Each transition is designed to land ON a beat, not through it. Default
durations are short (0.15-0.4s) so the transition completes within a beat.

Use via editor.crossfade_between(a, b, transition="whipblur", duration=0.2)
or directly via FFmpeg commands.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class FFmpegNotInstalled(RuntimeError):
    pass


def _ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FFmpegNotInstalled("ffmpeg not found on PATH.")
    return exe


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {proc.returncode})\ncmd: {' '.join(cmd)}\nstderr:\n{proc.stderr}"
        )


# xfade-supported transitions. Each key is our internal name.
# Value is either a plain xfade transition name OR a custom filter string
# that produces the transition via a different path.
_XFADE_MAP: dict[str, str] = {
    "fade": "fade",
    "fadeblack": "fadeblack",
    "fadewhite": "fadewhite",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "wipeup": "wipeup",
    "wipedown": "wipedown",
    "slideleft": "slideleft",
    "slideright": "slideright",
    "slideup": "slideup",
    "slidedown": "slidedown",
    "circlecrop": "circlecrop",
    "rectcrop": "rectcrop",
    "distance": "distance",
    "fadegrays": "fadegrays",
    "dissolve": "dissolve",
    "pixelize": "pixelize",
    "radial": "radial",
    "smoothleft": "smoothleft",
    "smoothright": "smoothright",
    "smoothup": "smoothup",
    "smoothdown": "smoothdown",
    "squeezeh": "squeezeh",
    "squeezev": "squeezev",
    "zoomin": "zoomin",
    "hblur": "hblur",
    "horzopen": "horzopen",
    "horzclose": "horzclose",
    "vertopen": "vertopen",
    "vertclose": "vertclose",
    "diagbl": "diagbl",
    "diagbr": "diagbr",
    "diagtl": "diagtl",
    "diagtr": "diagtr",
}


# Presets tuned for specific edit moods
TRANSITION_PRESETS: dict[str, dict[str, float | str]] = {
    # Hard cut is the default for music edits — no transition.
    "hard_cut": {"type": "none", "duration": 0.0},
    # Sharp flash between shots — great on beat drops
    "flash_white": {"type": "fadewhite", "duration": 0.08},
    "flash_black": {"type": "fadeblack", "duration": 0.08},
    # Fast whip feel
    "whip_left": {"type": "slideleft", "duration": 0.12},
    "whip_right": {"type": "slideright", "duration": 0.12},
    # Subtle crossfade for B-roll
    "soft_dissolve": {"type": "dissolve", "duration": 0.3},
    # Dramatic reveal
    "circle_in": {"type": "circlecrop", "duration": 0.4},
    # Glitchy pixelated
    "pixel_morph": {"type": "pixelize", "duration": 0.25},
    # Zoom punch-in (use xfade zoomin)
    "zoom_punch": {"type": "zoomin", "duration": 0.15},
    # Aesthetic slow fade
    "long_fade": {"type": "fade", "duration": 0.8},
}


def list_transitions() -> list[str]:
    return sorted(_XFADE_MAP.keys())


def list_presets() -> list[str]:
    return sorted(TRANSITION_PRESETS.keys())


def apply_transition(
    clip_a: str | Path,
    clip_b: str | Path,
    dst: str | Path,
    *,
    transition: str = "fade",
    duration: float = 0.3,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Apply an xfade transition between two clips.

    Clips are normalized to width x height (pad to fit, contain-mode) and fps
    before transitioning, so heterogeneous inputs compose cleanly.
    """
    if transition == "none" or duration <= 0:
        # No transition — just concatenate
        from .editor import concat
        return concat([clip_a, clip_b], dst)
    if transition not in _XFADE_MAP:
        raise ValueError(
            f"unknown transition '{transition}'. Try: {', '.join(list_transitions()[:10])}..."
        )

    from .editor import probe

    info_a = probe(clip_a)
    offset = max(0.0, info_a.duration - duration)

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    xfade_name = _XFADE_MAP[transition]

    fc = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}[va];"
        f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}[vb];"
        f"[va][vb]xfade=transition={xfade_name}:duration={duration}:offset={offset}[outv]"
    )
    _run([
        _ffmpeg_bin(), "-y",
        "-i", str(clip_a), "-i", str(clip_b),
        "-filter_complex", fc,
        "-map", "[outv]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
        "-an",
        str(dst),
    ])
    return dst


def apply_preset(
    clip_a: str | Path,
    clip_b: str | Path,
    dst: str | Path,
    preset: str,
) -> Path:
    """Apply a named transition preset (flash_white, whip_left, etc.)."""
    if preset not in TRANSITION_PRESETS:
        raise ValueError(f"unknown preset '{preset}'. Try: {', '.join(list_presets())}")
    cfg = TRANSITION_PRESETS[preset]
    return apply_transition(
        clip_a, clip_b, dst,
        transition=str(cfg["type"]),
        duration=float(cfg["duration"]),
    )


__all__ = [
    "FFmpegNotInstalled",
    "TRANSITION_PRESETS",
    "list_transitions",
    "list_presets",
    "apply_transition",
    "apply_preset",
]
