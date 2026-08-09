"""Color-grading presets for FFmpeg.

No external .cube LUTs required: each preset is a composable FFmpeg filter
chain tuned by hand to evoke a specific film/sensor look. Applied to video OR
still images.

Presets (all subtle — meant to complement a good image, not rescue a bad one):
- cinestill_800t     : cool shadows, warm highlights, red halation, grain
- portra_400         : warm skin, creamy highlights, soft rolloff
- tri_x_bw           : punchy B&W, grain, slight warm cast
- teal_orange        : popular cinema grade — shadows teal, skin orange
- moody_film         : crushed blacks, desaturated midtones, slight green cast
- kodachrome         : rich reds, yellow highlights, dark blues
- golden_hour        : warm push, lifted shadows, slight haze
- neon_night         : magenta/cyan push, cooler shadows, bloom
- faded_print        : lifted blacks, low saturation, slight sepia
- bleach_bypass      : high contrast, low saturation, harsh look
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


PRESETS: dict[str, str] = {
    # curve-based warm highlights + cool shadows, red halation, light grain
    "cinestill_800t": (
        "curves=r='0/0 0.3/0.25 0.7/0.78 1/1':g='0/0 0.5/0.48 1/0.98':b='0/0.02 0.4/0.35 0.7/0.68 1/0.95',"
        "eq=saturation=1.05:contrast=1.05,"
        "noise=alls=6:allf=t"
    ),
    "portra_400": (
        "curves=r='0/0.01 0.5/0.52 1/0.98':g='0/0.01 0.5/0.5 1/0.97':b='0/0 0.5/0.48 1/0.94',"
        "eq=saturation=0.95:contrast=0.98,"
        "noise=alls=3:allf=t"
    ),
    "tri_x_bw": (
        "hue=s=0,"
        "curves=master='0/0 0.15/0.08 0.5/0.52 0.85/0.92 1/1',"
        "eq=contrast=1.2,"
        "noise=alls=10:allf=t"
    ),
    "teal_orange": (
        "curves=r='0/0 0.5/0.55 1/1':b='0/0.08 0.5/0.48 1/0.92',"
        "eq=saturation=1.15:contrast=1.05"
    ),
    "moody_film": (
        "curves=master='0/0.02 0.2/0.12 0.8/0.82 1/0.96',"
        "eq=saturation=0.8:contrast=1.08,"
        "hue=h=-4"
    ),
    "kodachrome": (
        "curves=r='0/0 0.5/0.58 1/1':g='0/0 0.5/0.5 1/0.98':b='0/0 0.5/0.42 1/0.9',"
        "eq=saturation=1.2:contrast=1.08"
    ),
    "golden_hour": (
        "curves=r='0/0.02 0.5/0.56 1/1':b='0/0 0.5/0.42 1/0.88',"
        "eq=saturation=1.1:brightness=0.02"
    ),
    "neon_night": (
        "curves=r='0/0.02 0.5/0.55 1/1':b='0/0.05 0.5/0.55 1/1',"
        "eq=saturation=1.25:contrast=1.05,"
        "hue=h=8"
    ),
    "faded_print": (
        "curves=master='0/0.08 0.2/0.22 0.8/0.78 1/0.92',"
        "eq=saturation=0.7:contrast=0.9,"
        "hue=h=10"
    ),
    "bleach_bypass": (
        "curves=master='0/0 0.15/0.1 0.5/0.58 0.85/0.95 1/1',"
        "eq=saturation=0.5:contrast=1.2"
    ),
    # VHS-era tape look — lifted blacks, chroma bleed, slight noise
    "vhs_90s": (
        "curves=master='0/0.06 0.5/0.5 1/0.9',"
        "eq=saturation=0.85:contrast=0.92,"
        "hue=h=2,"
        "noise=alls=8:allf=t"
    ),
    # Deep teal highlights, analog feel
    "analog_teal": (
        "curves=r='0/0 0.5/0.48 1/0.95':g='0/0 0.5/0.52 1/1':b='0/0.05 0.5/0.58 1/1',"
        "eq=saturation=1.05:contrast=1.02"
    ),
    # Airy, pale, photographic — for daylight / lifestyle
    "high_key": (
        "curves=master='0/0.08 0.5/0.55 1/1',"
        "eq=saturation=0.85:brightness=0.04:contrast=0.95"
    ),
    # Flat documentary — low contrast, desaturated, neutral
    "doc_flat": (
        "curves=master='0/0.05 0.5/0.5 1/0.95',"
        "eq=saturation=0.7:contrast=0.9"
    ),
    # Punchy saturated sports look
    "sports_punch": (
        "curves=master='0/0 0.3/0.25 0.7/0.78 1/1',"
        "eq=saturation=1.3:contrast=1.12"
    ),
    # Warm golden-amber sepia-adjacent — nostalgic / memory
    "nostalgic_warm": (
        "curves=r='0/0.04 0.5/0.58 1/1':g='0/0.02 0.5/0.52 1/0.96':b='0/0 0.5/0.42 1/0.82',"
        "eq=saturation=0.9:contrast=0.98"
    ),
    # Cool corporate / tech — slight blue shift, high contrast
    "tech_cool": (
        "curves=r='0/0 0.5/0.48 1/0.98':b='0/0.04 0.5/0.54 1/1',"
        "eq=saturation=0.92:contrast=1.08"
    ),
    # Rich forest / gorpcore — green push, earthy shadows
    "forest_green": (
        "curves=r='0/0 0.5/0.48 1/0.95':g='0/0.03 0.5/0.55 1/1':b='0/0.02 0.5/0.48 1/0.94',"
        "eq=saturation=1.05:contrast=1.03"
    ),
    # Cyberpunk magenta-cyan
    "cyber_neon": (
        "curves=r='0/0.04 0.5/0.56 1/1':b='0/0.08 0.5/0.58 1/1',"
        "eq=saturation=1.4:contrast=1.1,"
        "hue=h=12"
    ),
}


def list_presets() -> list[str]:
    return sorted(PRESETS.keys())


def apply_preset(
    src: str | Path,
    dst: str | Path,
    preset: str,
) -> Path:
    """Apply a named grading preset to a video OR a still image."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset '{preset}'. Available: {', '.join(list_presets())}")
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = PRESETS[preset]

    is_image = src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    if is_image:
        _run([
            _ffmpeg_bin(), "-y",
            "-i", str(src),
            "-vf", vf,
            "-frames:v", "1",
            str(dst),
        ])
    else:
        _run([
            _ffmpeg_bin(), "-y",
            "-i", str(src),
            "-vf", vf,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            str(dst),
        ])
    return dst


__all__ = ["PRESETS", "list_presets", "apply_preset", "FFmpegNotInstalled"]
