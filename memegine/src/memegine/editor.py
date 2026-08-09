"""FFmpeg-based editor — the "no CapCut" layer.

Pure subprocess wrappers around FFmpeg + ffprobe. No external Python bindings.
Every function returns the path to the output file and raises a
``RuntimeError`` with stderr content on failure so callers can surface
actionable errors.

Supported operations:
- probe(path) -> width, height, duration
- to_aspect(src, ratio, dst, fit="cover") -> crop/pad to 9:16 / 1:1 / 16:9
- ken_burns(image, dst, duration=4, start_crop=..., end_crop=...) -> still -> video
- concat(clips, dst) -> stitch clips with hard cuts (re-encodes for safety)
- crossfade(clip_a, clip_b, dst, duration=0.3) -> two clips with xfade
- drawtext(src, dst, text, position="bottom", ...) -> burn caption onto video
- add_audio(src, audio, dst, mode="mix"|"replace", volume=1.0) -> attach audio
- speed(src, dst, factor) -> speed ramp
- lut(src, dst, lut_name) -> apply a built-in color grade preset

All functions are designed to be deterministic, idempotent (overwrites dst),
and safe to chain.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence


class FFmpegNotInstalled(RuntimeError):
    pass


def _ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FFmpegNotInstalled(
            "ffmpeg not found on PATH. Install it (`brew install ffmpeg` / "
            "`sudo apt install ffmpeg`) and try again."
        )
    return exe


def _ffprobe_bin() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise FFmpegNotInstalled("ffprobe not found on PATH.")
    return exe


def _run(cmd: Sequence[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {proc.returncode})\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc.stdout


@dataclass
class MediaInfo:
    width: int
    height: int
    duration: float
    has_audio: bool
    fps: float


def probe(path: str | Path) -> MediaInfo:
    path = Path(path)
    out = _run([
        _ffprobe_bin(),
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
    ])
    data = json.loads(out)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise RuntimeError(f"no video stream in {path}")
    width = int(video.get("width", 0))
    height = int(video.get("height", 0))
    duration = float(data.get("format", {}).get("duration", 0.0))
    fps_str = video.get("avg_frame_rate", "0/1")
    num, _, den = fps_str.partition("/")
    try:
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return MediaInfo(width=width, height=height, duration=duration, has_audio=audio is not None, fps=fps)


AspectRatio = Literal["9:16", "1:1", "16:9", "4:5"]

_ASPECT_DIMS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
    "4:5": (1080, 1350),
}


def to_aspect(
    src: str | Path,
    ratio: AspectRatio,
    dst: str | Path,
    fit: Literal["cover", "contain"] = "cover",
) -> Path:
    """Crop (cover) or pad (contain) src to the target aspect ratio."""
    if ratio not in _ASPECT_DIMS:
        raise ValueError(f"unsupported ratio: {ratio}")
    w, h = _ASPECT_DIMS[ratio]
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    if fit == "cover":
        # Scale so the shorter side fills, then center-crop to exact dims.
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    else:
        # contain: letterbox/pillarbox with black.
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"

    _run([
        _ffmpeg_bin(), "-y",
        "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
        "-c:a", "copy",
        str(dst),
    ])
    return dst


def ken_burns(
    image: str | Path,
    dst: str | Path,
    duration: float = 4.0,
    ratio: AspectRatio = "9:16",
    zoom_start: float = 1.0,
    zoom_end: float = 1.15,
    fps: int = 30,
) -> Path:
    """Turn a still into a short video via slow zoom (Ken Burns)."""
    w, h = _ASPECT_DIMS[ratio]
    src = Path(image)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    total_frames = max(1, int(duration * fps))
    # zoompan zoom expression goes from zoom_start to zoom_end over total_frames.
    zinc = (zoom_end - zoom_start) / total_frames
    vf = (
        f"scale={w * 4}:{h * 4}:force_original_aspect_ratio=increase,"
        f"crop={w * 4}:{h * 4},"
        f"zoompan=z='min(zoom+{zinc:.6f},{zoom_end})':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={total_frames}:s={w}x{h}:fps={fps}"
    )
    _run([
        _ffmpeg_bin(), "-y",
        "-loop", "1",
        "-i", str(src),
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
        str(dst),
    ])
    return dst


def concat(clips: Sequence[str | Path], dst: str | Path) -> Path:
    """Concatenate clips with hard cuts. Re-encodes (safe across heterogeneous inputs)."""
    if not clips:
        raise ValueError("clips must be non-empty")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Build filter_complex: scale each to 1080x1920 yuv420p so they concat cleanly.
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    n = len(clips)
    parts = []
    for i in range(n):
        parts.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30[v{i}];"
        )
    # audio: if a clip lacks audio, synthesize silence
    for i, c in enumerate(clips):
        info = probe(c)
        if info.has_audio:
            parts.append(f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS[a{i}];")
        else:
            parts.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=0:{info.duration}[a{i}];")
    concat_streams = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{concat_streams}concat=n={n}:v=1:a=1[outv][outa]")
    fc = "".join(parts)

    cmd = [
        _ffmpeg_bin(), "-y",
        *inputs,
        "-filter_complex", fc,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        str(dst),
    ]
    _run(cmd)
    return dst


def crossfade(
    clip_a: str | Path,
    clip_b: str | Path,
    dst: str | Path,
    duration: float = 0.3,
) -> Path:
    """Crossfade two clips over `duration` seconds. Use sparingly — hard cuts usually win."""
    info_a = probe(clip_a)
    offset = max(0.0, info_a.duration - duration)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    fc = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30[va];"
        f"[1:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30[vb];"
        f"[va][vb]xfade=transition=fade:duration={duration}:offset={offset}[outv]"
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


TextPosition = Literal["top", "bottom", "center"]


def drawtext(
    src: str | Path,
    dst: str | Path,
    text: str,
    *,
    position: TextPosition = "bottom",
    font_size: int = 64,
    font_color: str = "white",
    stroke_color: str = "black",
    stroke_width: int = 3,
    box: bool = False,
    box_color: str = "black@0.5",
    y_margin: int = 120,
) -> Path:
    """Burn a caption onto a video. Impact-style default (white w/ black stroke)."""
    if position == "top":
        y_expr = f"{y_margin}"
    elif position == "bottom":
        y_expr = f"h-text_h-{y_margin}"
    else:
        y_expr = "(h-text_h)/2"

    safe = (
        text.replace("\\", r"\\\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
            .replace(",", r"\,")
    )

    parts = [
        f"text='{safe}'",
        "x=(w-text_w)/2",
        f"y={y_expr}",
        f"fontsize={font_size}",
        f"fontcolor={font_color}",
        f"borderw={stroke_width}",
        f"bordercolor={stroke_color}",
    ]
    if box:
        parts += [f"box=1", f"boxcolor={box_color}", "boxborderw=24"]
    vf = f"drawtext={':'.join(parts)}"

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        _ffmpeg_bin(), "-y",
        "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
        "-c:a", "copy",
        str(dst),
    ])
    return dst


def add_audio(
    src: str | Path,
    audio: str | Path,
    dst: str | Path,
    *,
    mode: Literal["mix", "replace"] = "mix",
    volume: float = 1.0,
) -> Path:
    """Attach an audio track. mode=mix keeps existing audio and adds the track at `volume`."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    info = probe(src)

    if mode == "replace" or not info.has_audio:
        _run([
            _ffmpeg_bin(), "-y",
            "-i", str(src),
            "-i", str(audio),
            "-c:v", "copy",
            "-filter:a", f"volume={volume}",
            "-map", "0:v:0", "-map", "1:a:0",
            "-shortest",
            str(dst),
        ])
    else:
        fc = f"[1:a]volume={volume}[aux];[0:a][aux]amix=inputs=2:duration=first:dropout_transition=0[a]"
        _run([
            _ffmpeg_bin(), "-y",
            "-i", str(src), "-i", str(audio),
            "-filter_complex", fc,
            "-map", "0:v:0", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(dst),
        ])
    return dst


def speed(src: str | Path, dst: str | Path, factor: float) -> Path:
    """Speed ramp. factor > 1 = faster, < 1 = slower. Keeps pitch corrected on audio."""
    if factor <= 0:
        raise ValueError("factor must be > 0")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    info = probe(src)
    vf = f"setpts={1/factor}*PTS"
    atempo = _atempo_chain(factor)
    cmd = [_ffmpeg_bin(), "-y", "-i", str(src), "-vf", vf]
    if info.has_audio and atempo:
        cmd += ["-filter:a", atempo]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18", str(dst)]
    _run(cmd)
    return dst


def _atempo_chain(factor: float) -> str:
    """ffmpeg's atempo only supports 0.5-2.0 per filter; chain for extreme factors."""
    remaining = factor
    chain = []
    while remaining > 2.0:
        chain.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        chain.append("atempo=0.5")
        remaining /= 0.5
    chain.append(f"atempo={remaining}")
    return ",".join(chain)


__all__ = [
    "FFmpegNotInstalled",
    "MediaInfo",
    "probe",
    "to_aspect",
    "ken_burns",
    "concat",
    "crossfade",
    "drawtext",
    "add_audio",
    "speed",
]
