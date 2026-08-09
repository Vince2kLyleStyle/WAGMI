"""Variant renderer — take one master render and produce multiple outputs
along chosen axes (aspect ratio, color grade).

Useful for A/B posting: the same piece rendered as 9:16 / 1:1 + moody_film /
cinestill_800t gives you 4 variants to pick the winner for X.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from . import editor, grading, image_ops


def render_aspect_variants(
    src: str | Path,
    dst_dir: str | Path,
    *,
    ratios: Iterable[str] = ("9:16", "1:1", "16:9"),
    fit: str = "cover",
) -> list[Path]:
    """Render the same source into each aspect ratio, auto-routing image vs video."""
    src = Path(src)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    is_image = src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    outs: list[Path] = []
    for r in ratios:
        safe = r.replace(":", "x")
        dst = dst_dir / f"{src.stem}_{safe}{src.suffix}"
        if is_image:
            out = image_ops.to_aspect(src, r, dst, fit=fit)  # type: ignore[arg-type]
        else:
            out = editor.to_aspect(src, r, dst, fit=fit)  # type: ignore[arg-type]
        outs.append(Path(out))
    return outs


def render_grade_variants(
    src: str | Path,
    dst_dir: str | Path,
    *,
    presets: Iterable[str] = ("portra_400", "cinestill_800t", "teal_orange", "moody_film"),
) -> list[Path]:
    """Render the same source with each color-grade preset."""
    src = Path(src)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    outs: list[Path] = []
    for p in presets:
        dst = dst_dir / f"{src.stem}_{p}{src.suffix}"
        out = grading.apply_preset(src, dst, p)
        outs.append(Path(out))
    return outs


def render_matrix(
    src: str | Path,
    dst_dir: str | Path,
    *,
    ratios: Iterable[str] = ("9:16", "1:1"),
    presets: Iterable[str] = ("portra_400", "cinestill_800t"),
    fit: str = "cover",
) -> list[Path]:
    """Render all combinations of ratio x preset.

    Pipeline: source -> aspect -> grade. The grade is applied after the
    aspect change so the grade operates on the final crop/frame.
    """
    src = Path(src)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    is_image = src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    outs: list[Path] = []
    for r in ratios:
        safe = r.replace(":", "x")
        aspect_out = dst_dir / f"_tmp_{src.stem}_{safe}{src.suffix}"
        if is_image:
            image_ops.to_aspect(src, r, aspect_out, fit=fit)  # type: ignore[arg-type]
        else:
            editor.to_aspect(src, r, aspect_out, fit=fit)  # type: ignore[arg-type]
        for p in presets:
            dst = dst_dir / f"{src.stem}_{safe}_{p}{src.suffix}"
            grading.apply_preset(aspect_out, dst, p)
            outs.append(dst)
        # Clean up the aspect-only intermediate
        try:
            aspect_out.unlink()
        except OSError:
            pass
    return outs


__all__ = ["render_aspect_variants", "render_grade_variants", "render_matrix"]
