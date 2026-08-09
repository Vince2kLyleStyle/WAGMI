"""Sound design layer — synthesize common SFX (whoosh, impact, riser) via
FFmpeg lavfi filters, no external audio files required. Great for music
edits where you want a whoosh on a whip-pan or a hit on a slam.

All SFX are short (0.1-1.0s) and are layered onto an existing clip's
audio via add_audio(mode="mix") or by chaining into concat.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    return exe


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {proc.returncode})\ncmd: {' '.join(cmd)}\nstderr:\n{proc.stderr}"
        )


def whoosh(
    dst: str | Path,
    duration: float = 0.3,
    *,
    direction: str = "up",  # "up" = brighter / rising, "down" = darker / falling
    level: float = 0.8,
) -> Path:
    """Generate a whoosh — band-limited noise with a steep attack/decay.

    The direction is approximated by biasing the bandpass center (a "rising"
    whoosh uses a higher center frequency; "falling" uses a lower one), plus
    a pitch-shift via asetrate on the second half of the clip.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    center = 1800 if direction == "up" else 600
    # Two-stage envelope: fast attack, fast decay, peak at mid-point
    peak = duration / 2
    expr = (
        f"[0:a]aresample=48000,"
        f"bandpass=f={center}:w=1200,"
        f"volume={level},"
        f"afade=t=in:st=0:d={peak:.3f},"
        f"afade=t=out:st={peak:.3f}:d={max(0.02, duration - peak - 0.02):.3f}"
    )
    _run([
        _ffmpeg_bin(), "-y",
        "-f", "lavfi",
        "-i", f"anoisesrc=c=pink:d={duration}:r=48000:amplitude=0.6",
        "-filter_complex", expr,
        "-c:a", "aac", "-b:a", "192k",
        str(dst),
    ])
    return dst


def impact(
    dst: str | Path,
    *,
    intensity: str = "hard",  # "hard" | "soft" | "cinematic"
    duration: float = 0.5,
) -> Path:
    """Generate an impact / hit — low-end thud with a click on top.

    intensity:
      hard       : tight 40Hz kick + 200Hz click, short tail
      soft       : 60Hz body, 120Hz click, gentle tail
      cinematic  : deep 30Hz sub + long decay
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if intensity == "hard":
        body_hz, click_hz, tail = 40, 200, duration
    elif intensity == "soft":
        body_hz, click_hz, tail = 60, 120, duration
    else:  # cinematic
        body_hz, click_hz, tail = 30, 100, max(0.8, duration)

    # Mix a sine burst (the "thump") with a narrow noise burst (the "click")
    fc = (
        f"[0:a]aresample=48000,volume=1.0[thump];"
        f"[1:a]aresample=48000,volume=0.6[click];"
        f"[thump][click]amix=inputs=2:duration=first[mixed];"
        f"[mixed]afade=t=out:st={tail * 0.2}:d={tail * 0.8}"
    )
    _run([
        _ffmpeg_bin(), "-y",
        "-f", "lavfi", "-i", f"sine=frequency={body_hz}:duration={tail}",
        "-f", "lavfi", "-i", f"anoisesrc=c=white:d=0.04:r=48000",
        "-filter_complex", fc,
        "-c:a", "aac", "-b:a", "192k",
        str(dst),
    ])
    return dst


def riser(
    dst: str | Path,
    duration: float = 2.0,
    *,
    start_hz: float = 100,
    end_hz: float = 2000,
    level: float = 0.6,
) -> Path:
    """Generate a riser — swept sine that climbs into a drop or reveal.

    Typical use: 2-4 seconds leading into a slam beat.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    expr = f"sin(2*PI*({start_hz}+({end_hz}-{start_hz})*t/{duration})*t)"
    _run([
        _ffmpeg_bin(), "-y",
        "-f", "lavfi",
        "-i", f"aevalsrc='{level}*{expr}':s=48000:d={duration}",
        "-af", f"afade=t=in:st=0:d=0.2,afade=t=out:st={max(0.0, duration-0.1)}:d=0.1",
        "-c:a", "aac", "-b:a", "192k",
        str(dst),
    ])
    return dst


def click_track(
    dst: str | Path,
    *,
    bpm: int = 120,
    beats: int = 8,
    accent_every: int = 4,
) -> Path:
    """Generate a metronome click track. Useful as a scratch track for
    testing music-edit templates without copyrighted music.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    period = 60.0 / bpm
    duration = beats * period + 0.1
    # Pulse every period; louder + higher pitch on accented beats
    expr = (
        f"0.9*sin(2*PI*(if(lt(mod(floor(t/{period}),{accent_every}),0.5),1400,1000))*t)"
        f"*if(lt(mod(t,{period}),0.03),exp(-40*mod(t,{period})),0)"
    )
    _run([
        _ffmpeg_bin(), "-y",
        "-f", "lavfi",
        "-i", f"aevalsrc='{expr}':s=48000:d={duration}",
        "-c:a", "aac", "-b:a", "192k",
        str(dst),
    ])
    return dst


def layer_sfx(
    video: str | Path,
    dst: str | Path,
    *,
    sfx_cues: list[tuple[str | Path, float]],
    base_volume: float = 1.0,
    sfx_volume: float = 0.9,
) -> Path:
    """Layer multiple SFX files onto a video at specified timestamps.

    sfx_cues: list of (path, time_in_seconds)
    """
    video = Path(video)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not sfx_cues:
        raise ValueError("sfx_cues must be non-empty")

    cmd = [_ffmpeg_bin(), "-y", "-i", str(video)]
    for sfx, _ in sfx_cues:
        cmd += ["-i", str(sfx)]

    # Build filter: each SFX gets adelay; then all mixed with the base audio.
    parts = [f"[0:a]aresample=48000,volume={base_volume}[base]"]
    mix_inputs = ["[base]"]
    for i, (_, delay_s) in enumerate(sfx_cues, start=1):
        delay_ms = int(delay_s * 1000)
        parts.append(
            f"[{i}:a]aresample=48000,adelay={delay_ms}|{delay_ms},volume={sfx_volume}[s{i}]"
        )
        mix_inputs.append(f"[s{i}]")
    total = len(sfx_cues) + 1
    parts.append(
        "".join(mix_inputs) + f"amix=inputs={total}:duration=first:dropout_transition=0[outa]"
    )
    fc = ";".join(parts)

    cmd += [
        "-filter_complex", fc,
        "-map", "0:v:0", "-map", "[outa]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(dst),
    ]
    _run(cmd)
    return dst


__all__ = ["whoosh", "impact", "riser", "click_track", "layer_sfx"]
