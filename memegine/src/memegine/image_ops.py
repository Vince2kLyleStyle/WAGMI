"""Still-image operations: aspect-ratio crop/pad, caption compositing, grid
assembly. PIL-based. Complements editor.py (which handles video via FFmpeg).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont


AspectRatio = Literal["9:16", "1:1", "16:9", "4:5", "3:4"]

ASPECT_DIMS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
    "4:5": (1080, 1350),
    "3:4": (1080, 1440),
}


@dataclass
class ImageInfo:
    width: int
    height: int
    mode: str


def probe(path: str | Path) -> ImageInfo:
    with Image.open(path) as im:
        return ImageInfo(width=im.width, height=im.height, mode=im.mode)


def to_aspect(
    src: str | Path,
    ratio: AspectRatio,
    dst: str | Path,
    fit: Literal["cover", "contain"] = "cover",
    pad_color: str = "black",
) -> Path:
    """Crop (cover) or pad (contain) a still to a target aspect ratio."""
    if ratio not in ASPECT_DIMS:
        raise ValueError(f"unsupported ratio: {ratio}")
    tw, th = ASPECT_DIMS[ratio]
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as im:
        im = im.convert("RGB")
        sw, sh = im.size
        if fit == "cover":
            scale = max(tw / sw, th / sh)
            nw, nh = int(sw * scale), int(sh * scale)
            resized = im.resize((nw, nh), Image.LANCZOS)
            left = (nw - tw) // 2
            top = (nh - th) // 2
            out = resized.crop((left, top, left + tw, top + th))
        else:
            scale = min(tw / sw, th / sh)
            nw, nh = int(sw * scale), int(sh * scale)
            resized = im.resize((nw, nh), Image.LANCZOS)
            out = Image.new("RGB", (tw, th), pad_color)
            out.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
        out.save(dst, quality=95)
    return dst


TextPosition = Literal["top", "bottom", "center"]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Try a sequence of common fonts; fall back to PIL default."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVu-Sans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "C:/Windows/Fonts/impact.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def caption(
    src: str | Path,
    dst: str | Path,
    text: str,
    *,
    position: TextPosition = "bottom",
    font_size: int | None = None,
    font_color: str = "white",
    stroke_color: str = "black",
    stroke_width: int | None = None,
    y_margin: int | None = None,
    uppercase: bool = True,
) -> Path:
    """Compose a caption onto a still image in Impact-style white+black-stroke.

    Auto-sizes font relative to image width if font_size not given.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        fs = font_size or max(28, w // 18)
        sw = stroke_width if stroke_width is not None else max(2, fs // 16)
        ym = y_margin if y_margin is not None else max(40, h // 18)
        font = _load_font(fs)

        rendered = text.upper() if uppercase else text

        # Word-wrap to ~85% of image width
        max_px = int(w * 0.85)
        wrapped: list[str] = []
        for line in rendered.split("\n"):
            words = line.split()
            cur: list[str] = []
            for word in words:
                trial = " ".join(cur + [word])
                tw = _text_width(font, trial)
                if tw > max_px and cur:
                    wrapped.append(" ".join(cur))
                    cur = [word]
                else:
                    cur.append(word)
            if cur:
                wrapped.append(" ".join(cur))
            if not words:
                wrapped.append("")

        line_h = _line_height(font)
        total_h = line_h * len(wrapped)
        if position == "top":
            y = ym
        elif position == "bottom":
            y = h - total_h - ym
        else:
            y = (h - total_h) // 2

        draw = ImageDraw.Draw(im)
        for i, line in enumerate(wrapped):
            tw = _text_width(font, line)
            x = (w - tw) // 2
            draw.text(
                (x, y + i * line_h),
                line,
                font=font,
                fill=font_color,
                stroke_width=sw,
                stroke_fill=stroke_color,
            )
        im.save(dst, quality=95)
    return dst


def _text_width(font: ImageFont.FreeTypeFont, text: str) -> int:
    if hasattr(font, "getbbox"):
        l, _, r, _ = font.getbbox(text)
        return r - l
    return font.getsize(text)[0]  # type: ignore[attr-defined]


def _line_height(font: ImageFont.FreeTypeFont) -> int:
    if hasattr(font, "getbbox"):
        _, t, _, b = font.getbbox("Agjpqy")
        return int((b - t) * 1.15)
    return int(font.getsize("Agjpqy")[1] * 1.15)  # type: ignore[attr-defined]


def grid(
    images: list[str | Path],
    dst: str | Path,
    *,
    cols: int = 2,
    cell: tuple[int, int] = (540, 540),
    gap: int = 8,
    bg: str = "black",
) -> Path:
    """Pack a list of images into a grid (for Grok variant sheets)."""
    if not images:
        raise ValueError("images must be non-empty")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    rows = (len(images) + cols - 1) // cols
    cw, ch = cell
    W = cw * cols + gap * (cols - 1)
    H = ch * rows + gap * (rows - 1)
    canvas = Image.new("RGB", (W, H), bg)
    for i, p in enumerate(images):
        with Image.open(p) as im:
            im = im.convert("RGB")
            scale = max(cw / im.width, ch / im.height)
            nw, nh = int(im.width * scale), int(im.height * scale)
            resized = im.resize((nw, nh), Image.LANCZOS)
            left = (nw - cw) // 2
            top = (nh - ch) // 2
            tile = resized.crop((left, top, left + cw, top + ch))
        r, c = divmod(i, cols)
        canvas.paste(tile, (c * (cw + gap), r * (ch + gap)))
    canvas.save(dst, quality=95)
    return dst


def two_panel(
    top: str | Path,
    bottom: str | Path,
    dst: str | Path,
    *,
    ratio: AspectRatio = "4:5",
    gap: int = 8,
    bg: str = "white",
) -> Path:
    """Stack two images vertically for meme_two_panel-style layout."""
    tw, th = ASPECT_DIMS[ratio]
    cell_h = (th - gap) // 2
    with Image.open(top) as imt, Image.open(bottom) as imb:
        imt = imt.convert("RGB")
        imb = imb.convert("RGB")
        canvas = Image.new("RGB", (tw, th), bg)
        for i, im in enumerate([imt, imb]):
            scale = max(tw / im.width, cell_h / im.height)
            nw, nh = int(im.width * scale), int(im.height * scale)
            resized = im.resize((nw, nh), Image.LANCZOS)
            left = (nw - tw) // 2
            top_ = (nh - cell_h) // 2
            tile = resized.crop((left, top_, left + tw, top_ + cell_h))
            canvas.paste(tile, (0, i * (cell_h + gap)))
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst, quality=95)
    return dst


def blur_background_portrait(
    src: str | Path,
    dst: str | Path,
    *,
    ratio: AspectRatio = "9:16",
    blur: int = 40,
) -> Path:
    """Pad-to-portrait but fill the bars with a blurred copy of the image
    instead of solid black. Useful for 16:9 stills destined for 9:16 feeds.
    """
    tw, th = ASPECT_DIMS[ratio]
    with Image.open(src) as im:
        im = im.convert("RGB")
        sw, sh = im.size
        # Cover-scale for background blur
        scale_bg = max(tw / sw, th / sh)
        bg_w, bg_h = int(sw * scale_bg), int(sh * scale_bg)
        bg = im.resize((bg_w, bg_h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(blur))
        left_bg = (bg_w - tw) // 2
        top_bg = (bg_h - th) // 2
        bg = bg.crop((left_bg, top_bg, left_bg + tw, top_bg + th))
        # Contain-scale for foreground
        scale_fg = min(tw / sw, th / sh)
        fw, fh = int(sw * scale_fg), int(sh * scale_fg)
        fg = im.resize((fw, fh), Image.LANCZOS)
        bg.paste(fg, ((tw - fw) // 2, (th - fh) // 2))
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    bg.save(dst, quality=95)
    return dst


__all__ = [
    "ASPECT_DIMS",
    "ImageInfo",
    "probe",
    "to_aspect",
    "caption",
    "grid",
    "two_panel",
    "blur_background_portrait",
]
