from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from . import (
    archive,
    audio as audio_mod,
    capture as capture_mod,
    copy_writer,
    editor,
    grading,
    image_ops,
    linter,
    music_edit,
    pipeline as pipeline_mod,
    prompt_engine,
    reference_lib,
    render_variants,
    reverse_engineer,
    scorer,
    sfx as sfx_mod,
    shot_list,
    style_codex,
    telegram_bot as telegram_mod,
    transitions as transitions_mod,
    variants as variants_mod,
)
from .config import settings

app = typer.Typer(
    help="Memegine — director's assistant for elite photo/video content. "
         "Offline-first: prints assembled prompts for you to paste into Claude Code or Grok.",
    no_args_is_help=True,
)
console = Console()


def _print_prompt(system: str, user: str, title: str) -> None:
    console.print(Panel.fit(f"[bold cyan]{title}[/]", border_style="cyan"))
    console.print(Panel(system, title="SYSTEM", border_style="magenta"))
    console.print(Panel(user, title="USER (paste into Claude Code / Claude.ai)", border_style="green"))


@app.command("formats")
def list_formats() -> None:
    """List available formats from the library."""
    for f in prompt_engine.load_formats():
        console.print(f"[bold]{f.slug}[/]  [dim]({f.kind})[/]")
        console.print(f"  {f.description.splitlines()[0] if f.description else ''}")


@app.command("prompt")
def make_prompt(
    intent: str = typer.Argument(..., help="Rough operator intent, in quotes."),
    format: str = typer.Option(..., "--format", "-f", help="Format slug. Run `memegine formats` to list."),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tags to pull reference notes."),
) -> None:
    """Assemble a production-grade brief and print it for Claude Code / Claude.ai.

    You paste the SYSTEM + USER blocks into any Claude session; Claude returns the
    JSON brief; you paste the 'prompt' field into Grok Imagine.
    """
    settings.ensure_dirs()
    ref_tags = [t.strip() for t in tags.split(",")] if tags else None
    ref_notes = reference_lib.reference_notes_for_prompt(tags=ref_tags) if ref_tags else ""
    system, user = prompt_engine.assemble_offline_prompt(intent, format, reference_notes=ref_notes)
    archive.save(kind="prompt", intent=intent, system=system, user=user, format_=format)
    _print_prompt(system, user, f"Prompt brief — format: {format}")


@app.command("pipeline")
def run_pipeline(
    intent: str = typer.Argument(..., help="Rough operator intent for the piece."),
    kind: str = typer.Option(..., "--kind", "-k", help="image | video"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Required for kind=image."),
    no_copy: bool = typer.Option(False, "--no-copy", help="Skip the X caption brief."),
) -> None:
    """One command, one folder, every brief for a whole piece.

    For kind=image: produces prompt brief + copy brief.
    For kind=video: produces shot-list brief + copy brief (the shot-list already
    contains per-shot still + motion prompts).
    """
    settings.ensure_dirs()
    bundle = pipeline_mod.build(
        intent,
        kind=kind,
        format_slug=format,
        include_copy=not no_copy,
    )
    console.print(Panel.fit(f"[bold green]Bundle {bundle.id}[/]  kind={bundle.kind}", border_style="green"))
    console.print(f"folder: [cyan]{bundle.folder}[/]")
    for name in bundle.briefs:
        console.print(f"  - {name} brief -> [dim]{name in bundle.briefs}[/]")
    console.print("\nOpen each `.md` in order and paste into Claude Code.")


@app.command("variants")
def make_variants(
    winner_prompt: str = typer.Argument(..., help="The winning prompt string to vary."),
    n: int = typer.Option(6, "-n", help="How many variants to produce."),
) -> None:
    """Assemble a brief that asks Claude to vary a winning prompt along taxonomy axes."""
    settings.ensure_dirs()
    vb = variants_mod.build_variant_brief(winner_prompt, n_variants=n)
    archive.save(kind="variants", intent=winner_prompt, system=vb.system, user=vb.user, extra={"n": n})
    _print_prompt(vb.system, vb.user, f"Variants brief — n={n}")


@app.command("reverse")
def reverse_brief(
    image: Path = typer.Argument(..., help="Path to an image you want the 'look' of."),
    context: str = typer.Option("", "--context", "-c", help="Optional operator note about what you liked."),
) -> None:
    """Generate a brief that asks Claude to analyze an image and produce a recreate-the-look prompt."""
    settings.ensure_dirs()
    system, user = reverse_engineer.build_reverse_brief(image, context=context)
    archive.save(kind="reverse", intent=str(image), system=system, user=user, extra={"context": context})
    _print_prompt(system, user, f"Reverse brief — {image.name}")


@app.command("lint")
def lint_prompt(
    prompt: str = typer.Argument(..., help="The prompt string to lint (paste in quotes)."),
    motion: bool = typer.Option(False, "--motion", help="Lint as a motion prompt (requires camera move)."),
) -> None:
    """Run the prompt linter. Fails with non-zero exit on banned words."""
    result = linter.lint(prompt, kind="motion" if motion else "image")
    console.print(result.as_text())
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("score")
def score_prompt(
    prompt: str = typer.Argument(..., help="Production prompt to score."),
    motion: bool = typer.Option(False, "--motion", help="Score as a motion prompt."),
) -> None:
    """Score a production prompt 0-100 against craft criteria.

    Use as a gate before pasting into Grok — revise anything under 70.
    """
    result = scorer.score(prompt, kind="motion" if motion else "image")
    console.print(result.as_text())
    if result.score < 55:
        raise typer.Exit(code=1)


render_app = typer.Typer(help="Render variants from a master file (aspect x grade matrix).")
app.add_typer(render_app, name="render")


@render_app.command("aspects")
def render_aspects(
    src: Path = typer.Argument(..., exists=True, readable=True),
    dst_dir: Path = typer.Argument(...),
    ratios: str = typer.Option("9:16,1:1,16:9", "--ratios", help="Comma-separated aspect ratios."),
    fit: str = typer.Option("cover", "--fit"),
) -> None:
    """Render into multiple aspect ratios."""
    rs = tuple(r.strip() for r in ratios.split(","))
    outs = render_variants.render_aspect_variants(src, dst_dir, ratios=rs, fit=fit)
    for p in outs:
        console.print(f"[green]wrote[/] {p}")


@render_app.command("grades")
def render_grades(
    src: Path = typer.Argument(..., exists=True, readable=True),
    dst_dir: Path = typer.Argument(...),
    presets: str = typer.Option(
        "portra_400,cinestill_800t,teal_orange,moody_film",
        "--presets",
        help="Comma-separated grading presets.",
    ),
) -> None:
    """Render with multiple color-grading presets."""
    ps = tuple(p.strip() for p in presets.split(","))
    outs = render_variants.render_grade_variants(src, dst_dir, presets=ps)
    for p in outs:
        console.print(f"[green]wrote[/] {p}")


@render_app.command("matrix")
def render_matrix(
    src: Path = typer.Argument(..., exists=True, readable=True),
    dst_dir: Path = typer.Argument(...),
    ratios: str = typer.Option("9:16,1:1", "--ratios"),
    presets: str = typer.Option("portra_400,cinestill_800t", "--presets"),
    fit: str = typer.Option("cover", "--fit"),
) -> None:
    """Render the full aspect x grade matrix (R x P outputs)."""
    rs = tuple(r.strip() for r in ratios.split(","))
    ps = tuple(p.strip() for p in presets.split(","))
    outs = render_variants.render_matrix(src, dst_dir, ratios=rs, presets=ps, fit=fit)
    for p in outs:
        console.print(f"[green]wrote[/] {p}")


@app.command("shots")
def make_shots(
    intent: str = typer.Argument(..., help="Rough operator intent for the video."),
) -> None:
    """Assemble a shot-list brief for short video pieces."""
    settings.ensure_dirs()
    system, user = shot_list.assemble_offline_shot_list_prompt(intent)
    archive.save(kind="shots", intent=intent, system=system, user=user)
    _print_prompt(system, user, "Shot list brief")


@app.command("copy")
def make_copy(
    concept: str = typer.Argument(..., help="Describe the piece (what's in the image/video)."),
    kind: str = typer.Option("image", "--kind", "-k", help="image | video"),
) -> None:
    """Assemble a caption-writer brief for the finished piece."""
    settings.ensure_dirs()
    system, user = copy_writer.assemble_offline_copy_prompt(concept, kind)
    archive.save(kind="copy", intent=concept, system=system, user=user)
    _print_prompt(system, user, "X caption brief")


history_app = typer.Typer(help="Browse the brief archive.")
app.add_typer(history_app, name="history")


@history_app.command("recent")
def history_recent(n: int = typer.Option(10, "-n")) -> None:
    """Show the N most recent briefs."""
    rows = archive.read_recent(n)
    if not rows:
        console.print("[dim]no briefs yet[/]")
        return
    for r in rows:
        fmt = r.get("format") or "-"
        console.print(
            f"[bold]{r['id']}[/] {r['created_at'][:19]}  kind={r['kind']:<8} fmt={fmt:<24} intent={r['intent'][:70]}"
        )


@history_app.command("show")
def history_show(brief_id: str = typer.Argument(..., help="Brief id (prefix)")) -> None:
    """Print a single archived brief by id."""
    rec = archive.find(brief_id)
    if rec is None:
        # also try prefix match
        for candidate in archive.read_recent(200):
            if candidate["id"].startswith(brief_id):
                rec = candidate
                break
    if rec is None:
        console.print(f"[red]not found:[/] {brief_id}")
        raise typer.Exit(code=1)
    console.print(Panel.fit(f"[bold]{rec['id']}[/] {rec['created_at']}", border_style="cyan"))
    console.print(f"kind: {rec['kind']}  format: {rec.get('format') or '-'}")
    console.print(f"intent: {rec['intent']}")
    console.print(Panel(rec["system"], title="SYSTEM", border_style="magenta"))
    console.print(Panel(rec["user"], title="USER", border_style="green"))


@history_app.command("search")
def history_search(text: str = typer.Argument(..., help="Substring to search in intent/user body.")) -> None:
    """Search archived briefs by substring."""
    for r in archive.search(text):
        console.print(f"[bold]{r['id']}[/] {r['created_at'][:19]}  {r['intent'][:90]}")


codex_app = typer.Typer(help="Read and update the style codex.")
app.add_typer(codex_app, name="codex")


@codex_app.command("show")
def codex_show() -> None:
    """Print the current style codex."""
    text = style_codex.read()
    if not text:
        console.print("[dim]Codex is empty. It lives at: " + str(settings.codex_path) + "[/]")
        return
    console.print(Syntax(text, "markdown", theme="monokai", word_wrap=True))


@codex_app.command("winner")
def codex_winner(
    prompt: str = typer.Argument(..., help="The prompt that worked."),
    why: str = typer.Argument(..., help="Why it worked — 1 line."),
) -> None:
    """Append a winning prompt pattern to the codex."""
    style_codex.log_winner(prompt, why)
    console.print(f"[green]Logged winner to[/] {settings.codex_path}")


@codex_app.command("flop")
def codex_flop(
    what: str = typer.Argument(..., help="What flopped."),
    why: str = typer.Argument(..., help="Why — 1 line."),
) -> None:
    """Append to the kill list."""
    style_codex.log_flop(what, why)
    console.print(f"[yellow]Logged flop to[/] {settings.codex_path}")


edit_app = typer.Typer(help="FFmpeg-backed video editing (no CapCut).")
app.add_typer(edit_app, name="edit")


@edit_app.command("aspect")
def edit_aspect(
    src: Path = typer.Argument(..., exists=True, readable=True),
    dst: Path = typer.Argument(...),
    ratio: str = typer.Option("9:16", "--ratio", "-r", help="9:16, 1:1, 16:9, 4:5"),
    fit: str = typer.Option("cover", "--fit", help="cover (crop) | contain (pad)"),
) -> None:
    """Reframe a clip or still to a target aspect ratio."""
    if src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        out = image_ops.to_aspect(src, ratio, dst, fit=fit)  # type: ignore[arg-type]
    else:
        out = editor.to_aspect(src, ratio, dst, fit=fit)  # type: ignore[arg-type]
    console.print(f"[green]wrote[/] {out}")


@edit_app.command("kenburns")
def edit_kenburns(
    image: Path = typer.Argument(..., exists=True, readable=True),
    dst: Path = typer.Argument(...),
    duration: float = typer.Option(4.0, "--duration", "-d"),
    ratio: str = typer.Option("9:16", "--ratio", "-r"),
    zoom_start: float = typer.Option(1.0),
    zoom_end: float = typer.Option(1.15),
) -> None:
    """Turn a still into a Ken Burns video."""
    out = editor.ken_burns(image, dst, duration=duration, ratio=ratio, zoom_start=zoom_start, zoom_end=zoom_end)  # type: ignore[arg-type]
    console.print(f"[green]wrote[/] {out}")


@edit_app.command("concat")
def edit_concat(
    dst: Path = typer.Argument(...),
    clips: list[Path] = typer.Argument(..., help="Clips in order"),
) -> None:
    """Stitch clips with hard cuts."""
    out = editor.concat(clips, dst)
    console.print(f"[green]wrote[/] {out}")


@edit_app.command("caption")
def edit_caption(
    src: Path = typer.Argument(..., exists=True, readable=True),
    dst: Path = typer.Argument(...),
    text: str = typer.Argument(...),
    position: str = typer.Option("bottom", "--pos", help="top | bottom | center"),
    size: int = typer.Option(0, "--size", help="Font size. 0 = auto from image width."),
) -> None:
    """Burn a caption onto a still or video."""
    if src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        fs = size or None
        out = image_ops.caption(src, dst, text, position=position, font_size=fs)  # type: ignore[arg-type]
    else:
        fs = size or 64
        out = editor.drawtext(src, dst, text, position=position, font_size=fs)  # type: ignore[arg-type]
    console.print(f"[green]wrote[/] {out}")


@edit_app.command("grade")
def edit_grade(
    src: Path = typer.Argument(..., exists=True, readable=True),
    dst: Path = typer.Argument(...),
    preset: str = typer.Option(..., "--preset", "-p", help="e.g. portra_400, cinestill_800t"),
) -> None:
    """Apply a color-grading preset."""
    out = grading.apply_preset(src, dst, preset)
    console.print(f"[green]wrote[/] {out}")


@edit_app.command("presets")
def edit_presets() -> None:
    """List available grading presets."""
    for p in grading.list_presets():
        console.print(f"  {p}")


@edit_app.command("speed")
def edit_speed(
    src: Path = typer.Argument(..., exists=True, readable=True),
    dst: Path = typer.Argument(...),
    factor: float = typer.Argument(..., help="e.g. 2.0 = 2x speed, 0.5 = half"),
) -> None:
    """Speed ramp a clip (keeps audio pitch-corrected)."""
    out = editor.speed(src, dst, factor)
    console.print(f"[green]wrote[/] {out}")


@edit_app.command("audio")
def edit_audio(
    src: Path = typer.Argument(..., exists=True, readable=True),
    audio: Path = typer.Argument(..., exists=True, readable=True),
    dst: Path = typer.Argument(...),
    mode: str = typer.Option("mix", "--mode", help="mix | replace"),
    volume: float = typer.Option(1.0, "--volume"),
) -> None:
    """Attach an audio track (mix with existing, or replace)."""
    out = editor.add_audio(src, audio, dst, mode=mode, volume=volume)  # type: ignore[arg-type]
    console.print(f"[green]wrote[/] {out}")


@edit_app.command("grid")
def edit_grid(
    dst: Path = typer.Argument(...),
    images: list[Path] = typer.Argument(..., help="Images to grid together"),
    cols: int = typer.Option(2, "--cols"),
) -> None:
    """Pack images into a grid (for Grok variant sheets)."""
    out = image_ops.grid(images, dst, cols=cols)
    console.print(f"[green]wrote[/] {out}")


@edit_app.command("two-panel")
def edit_two_panel(
    top: Path = typer.Argument(..., exists=True, readable=True),
    bottom: Path = typer.Argument(..., exists=True, readable=True),
    dst: Path = typer.Argument(...),
) -> None:
    """Stack two images into a meme two-panel layout (4:5)."""
    out = image_ops.two_panel(top, bottom, dst)
    console.print(f"[green]wrote[/] {out}")


music_app = typer.Typer(help="Music-synced edits. Beat detection + template rendering.")
app.add_typer(music_app, name="music")


@music_app.command("plan")
def music_plan(
    audio_file: Path = typer.Argument(..., exists=True, readable=True),
    intent: str = typer.Argument(..., help="Rough intent for the edit."),
    clips: list[Path] = typer.Argument(..., help="Available clip files, in order."),
) -> None:
    """Assemble a music-edit planning brief (paste into Claude Code / Claude.ai).

    Analyzes the audio, reads the clip list, and prints the SYSTEM + USER
    blocks that ask Claude to plan the whole edit (template, segments,
    transitions, text overlays, exact CLI command).
    """
    from . import music_brief
    settings.ensure_dirs()
    grid = audio_mod.analyze(audio_file)
    drop = audio_mod.find_drop(grid)
    clip_descs = [str(c) for c in clips]
    system, user = music_brief.build_music_brief(
        intent,
        music_metadata={
            "path": str(audio_file),
            "tempo_bpm": grid.tempo_bpm,
            "duration_sec": grid.duration,
            "beats": grid.beats,
            "drop_sec": drop,
        },
        clips_description=clip_descs,
    )
    archive.save(kind="music_plan", intent=intent, system=system, user=user, extra={"clips": clip_descs})
    _print_prompt(system, user, "Music-edit plan brief")


@music_app.command("beats")
def music_beats(
    audio_file: Path = typer.Argument(..., exists=True, readable=True, help="Audio file (mp3/wav/m4a)."),
) -> None:
    """Print detected BPM + beat timestamps."""
    grid = audio_mod.analyze(audio_file)
    console.print(f"[bold]{audio_file.name}[/]")
    console.print(f"  tempo: [cyan]{grid.tempo_bpm:.1f} BPM[/]  duration: {grid.duration:.2f}s  beats: {len(grid.beats)}")
    console.print(f"  avg interval: {grid.avg_beat_interval:.3f}s")
    console.print(f"  first 12 beats: {[f'{t:.2f}' for t in grid.beats[:12]]}")
    drop = audio_mod.find_drop(grid)
    if drop is not None:
        console.print(f"  estimated drop: [yellow]{drop:.2f}s[/]")


@music_app.command("hardcut")
def music_hardcut(
    dst: Path = typer.Argument(...),
    music: Path = typer.Argument(..., exists=True, readable=True),
    clips: list[Path] = typer.Argument(..., help="Clips in order (will be cycled if fewer than beats allow)."),
    beats_per_cut: int = typer.Option(1, "--bpc", help="Beats between cuts (1 = cut every beat; 2 = every 2 beats)."),
    start_beat: int = typer.Option(0, "--start", help="Which beat index to begin on."),
) -> None:
    """Hard-cut montage: one cut per beat (or per N beats). Music carried through."""
    out = music_edit.hard_cut_montage(
        clips, music, dst, beats_per_cut=beats_per_cut, start_beat=start_beat,
    )
    console.print(f"[green]wrote[/] {out}")


@music_app.command("build")
def music_build(
    dst: Path = typer.Argument(...),
    music: Path = typer.Argument(..., exists=True, readable=True),
    clips: list[Path] = typer.Argument(...),
    start_beat: int = typer.Option(0, "--start"),
    start_per_cut: int = typer.Option(4, "--start-per-cut"),
    end_per_cut: int = typer.Option(1, "--end-per-cut"),
) -> None:
    """Rhythmic build: cuts accelerate from long to short."""
    out = music_edit.rhythmic_build(
        clips, music, dst,
        start_beat=start_beat, start_per_cut=start_per_cut, end_per_cut=end_per_cut,
    )
    console.print(f"[green]wrote[/] {out}")


@music_app.command("slam")
def music_slam(
    dst: Path = typer.Argument(...),
    music: Path = typer.Argument(..., exists=True, readable=True),
    clip: Path = typer.Argument(..., exists=True, readable=True),
    slam: float = typer.Option(-1.0, "--slam", help="Time of the slam beat in seconds. Negative = auto-detect drop."),
    ramp_in: float = typer.Option(1.5, "--ramp-in", help="Seconds of slow-mo before the slam."),
    slow: float = typer.Option(0.4, "--slow", help="Slow-mo factor (0.4 = 40% speed)."),
    post: float = typer.Option(1.0, "--post", help="Seconds at normal speed after the slam."),
) -> None:
    """Slow-mo speed ramp into a beat, snap to normal speed on the beat."""
    slam_val = None if slam < 0 else slam
    out = music_edit.speed_ramp_slam(
        clip, music, dst,
        slam_beat_sec=slam_val, ramp_in_sec=ramp_in, slow_factor=slow, post_slam_sec=post,
    )
    console.print(f"[green]wrote[/] {out}")


@music_app.command("impact")
def music_impact(
    dst: Path = typer.Argument(...),
    music: Path = typer.Argument(..., exists=True, readable=True),
    clips: list[Path] = typer.Argument(...),
    flash: str = typer.Option("white", "--flash", help="white | black"),
    flash_frames: int = typer.Option(2, "--frames"),
    beats_per_cut: int = typer.Option(1, "--bpc"),
) -> None:
    """Hard cuts between clips with a N-frame flash on each transition."""
    out = music_edit.impact_frame_chain(
        clips, music, dst,
        flash_color=flash, flash_frames=flash_frames, beats_per_cut=beats_per_cut,
    )
    console.print(f"[green]wrote[/] {out}")


@music_app.command("reveal")
def music_reveal(
    dst: Path = typer.Argument(...),
    music: Path = typer.Argument(..., exists=True, readable=True),
    clip: Path = typer.Argument(..., exists=True, readable=True),
    duration: float = typer.Option(8.0, "--duration", "-d"),
    audio_start: float = typer.Option(0.0, "--audio-start"),
) -> None:
    """One clip or still, slow push-in, music underneath. The 'vibe' template."""
    out = music_edit.aesthetic_slow_reveal(
        clip, music, dst, duration=duration, audio_start=audio_start,
    )
    console.print(f"[green]wrote[/] {out}")


@music_app.command("trailer")
def music_trailer(
    dst: Path = typer.Argument(...),
    music: Path = typer.Argument(..., exists=True, readable=True),
    clips: list[Path] = typer.Argument(..., help="Last clip is the hero held through the slam."),
    slam: float = typer.Option(-1.0, "--slam", help="Slam time in seconds. Negative = auto-detect."),
    pre_build: float = typer.Option(6.0, "--pre-build"),
    post_slam: float = typer.Option(2.0, "--post-slam"),
) -> None:
    """Trailer: long cuts -> accelerating -> slam -> held hero shot."""
    slam_val = None if slam < 0 else slam
    out = music_edit.trailer_build(
        clips, music, dst,
        slam_beat_sec=slam_val, pre_build_seconds=pre_build, post_slam_seconds=post_slam,
    )
    console.print(f"[green]wrote[/] {out}")


@music_app.command("transitions")
def music_list_transitions() -> None:
    """List available transition presets."""
    console.print("[bold]Transition presets[/]")
    for p in transitions_mod.list_presets():
        cfg = transitions_mod.TRANSITION_PRESETS[p]
        console.print(f"  {p}  ({cfg['type']}, {cfg['duration']}s)")
    console.print("\n[bold]Raw xfade types[/]")
    console.print("  " + ", ".join(transitions_mod.list_transitions()))


@music_app.command("transition")
def music_transition(
    dst: Path = typer.Argument(...),
    clip_a: Path = typer.Argument(..., exists=True, readable=True),
    clip_b: Path = typer.Argument(..., exists=True, readable=True),
    preset: str = typer.Option(..., "--preset", "-p", help="e.g. flash_white, whip_left, zoom_punch"),
) -> None:
    """Apply a single transition between two clips (preview / testing use)."""
    out = transitions_mod.apply_preset(clip_a, clip_b, dst, preset)
    console.print(f"[green]wrote[/] {out}")


sfx_app = typer.Typer(help="SFX synthesizers (whoosh, impact, riser, click).")
app.add_typer(sfx_app, name="sfx")


@sfx_app.command("whoosh")
def sfx_whoosh(
    dst: Path = typer.Argument(...),
    duration: float = typer.Option(0.3, "-d"),
    direction: str = typer.Option("up", help="up | down"),
    level: float = typer.Option(0.8, "--level"),
) -> None:
    """Synthesize a whoosh."""
    out = sfx_mod.whoosh(dst, duration=duration, direction=direction, level=level)
    console.print(f"[green]wrote[/] {out}")


@sfx_app.command("impact")
def sfx_impact(
    dst: Path = typer.Argument(...),
    intensity: str = typer.Option("hard", help="hard | soft | cinematic"),
    duration: float = typer.Option(0.5, "-d"),
) -> None:
    """Synthesize an impact / hit."""
    out = sfx_mod.impact(dst, intensity=intensity, duration=duration)
    console.print(f"[green]wrote[/] {out}")


@sfx_app.command("riser")
def sfx_riser(
    dst: Path = typer.Argument(...),
    duration: float = typer.Option(2.0, "-d"),
    start_hz: float = typer.Option(100.0, "--start-hz"),
    end_hz: float = typer.Option(2000.0, "--end-hz"),
) -> None:
    """Synthesize a riser (swept tone)."""
    out = sfx_mod.riser(dst, duration=duration, start_hz=start_hz, end_hz=end_hz)
    console.print(f"[green]wrote[/] {out}")


@sfx_app.command("click")
def sfx_click(
    dst: Path = typer.Argument(...),
    bpm: int = typer.Option(120, "--bpm"),
    beats: int = typer.Option(8, "--beats"),
) -> None:
    """Synthesize a metronome click track (for music-edit testing)."""
    out = sfx_mod.click_track(dst, bpm=bpm, beats=beats)
    console.print(f"[green]wrote[/] {out}")


@sfx_app.command("layer")
def sfx_layer(
    video: Path = typer.Argument(..., exists=True, readable=True),
    dst: Path = typer.Argument(...),
    cue: list[str] = typer.Option(
        ...,
        "--cue",
        help="Format: <sfx_path>:<time_sec>. Repeat for multiple cues.",
    ),
) -> None:
    """Layer multiple SFX onto a video at specified timestamps.

    Example: --cue whoosh.m4a:0.5 --cue impact.m4a:1.2
    """
    cues: list[tuple[Path, float]] = []
    for c in cue:
        path_str, _, time_str = c.rpartition(":")
        cues.append((Path(path_str), float(time_str)))
    out = sfx_mod.layer_sfx(video, dst, sfx_cues=cues)
    console.print(f"[green]wrote[/] {out}")


capture_app = typer.Typer(help="Idea queue — save rough thoughts for later briefs.")
app.add_typer(capture_app, name="capture")


@capture_app.command("add")
def capture_add(
    intent: str = typer.Argument(..., help="Rough thought to save."),
) -> None:
    """Quick-save a rough thought to the idea queue."""
    settings.ensure_dirs()
    c = capture_mod.add(intent)
    console.print(f"[green]captured[/] {c.id}  {c.created_at[:19]}")


@capture_app.command("list")
def capture_list(
    all_: bool = typer.Option(False, "--all", help="Include consumed/discarded"),
) -> None:
    """List captures (pending by default; --all for everything)."""
    rows = capture_mod.list_all() if all_ else capture_mod.list_pending()
    if not rows:
        console.print("[dim]queue is empty[/]")
        return
    for c in rows:
        marker = {"pending": "[cyan]●[/]", "consumed": "[green]✓[/]", "discarded": "[red]✗[/]"}.get(c.status, "?")
        console.print(f"{marker} [bold]{c.id}[/]  {c.created_at[:19]}  {c.intent[:90]}")


@capture_app.command("discard")
def capture_discard(capture_id: str = typer.Argument(...)) -> None:
    """Mark a capture as discarded."""
    res = capture_mod.discard(capture_id)
    if res is None:
        console.print(f"[red]not found:[/] {capture_id}")
        raise typer.Exit(code=1)
    console.print(f"[yellow]discarded[/] {res.id}")


@capture_app.command("consume")
def capture_consume(
    capture_id: str = typer.Argument(...),
    format: str = typer.Option(..., "--format", "-f"),
) -> None:
    """Convert a capture into a full piece brief (runs pipeline + marks consumed)."""
    c = capture_mod.find(capture_id)
    if c is None:
        console.print(f"[red]not found:[/] {capture_id}")
        raise typer.Exit(code=1)
    settings.ensure_dirs()
    bundle = pipeline_mod.build(c.intent, kind="image", format_slug=format, include_copy=True)
    capture_mod.mark_consumed(c.id, bundle.id)
    console.print(f"[green]consumed[/] {c.id} -> bundle {bundle.id}  folder={bundle.folder}")


telegram_app = typer.Typer(help="Telegram bot (cockpit).")
app.add_typer(telegram_app, name="telegram")


@telegram_app.command("run")
def telegram_run() -> None:  # pragma: no cover
    """Start the Telegram bot in polling mode.

    Requires env vars:
      TELEGRAM_BOT_TOKEN
      MEMEGINE_TELEGRAM_OPERATOR_CHAT_ID
    """
    telegram_mod.run()


refs_app = typer.Typer(help="Manage the reference library.")
app.add_typer(refs_app, name="refs")


@refs_app.command("add")
def refs_add(
    image: Path = typer.Argument(..., exists=True, readable=True, help="Path to image file."),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tags."),
    source: str = typer.Option("", "--source", help="Where this came from, e.g. 'grok', 'shot'."),
    prompt: str = typer.Option("", "--prompt", help="Prompt that produced it, if known."),
    notes: str = typer.Option("", "--notes", help="Why it's a keeper."),
) -> None:
    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    entry = reference_lib.add(image, tags=tag_list, source=source, prompt=prompt, notes=notes)
    console.print(f"[green]Added ref[/] {entry.id} → {entry.filename}")


@refs_app.command("search")
def refs_search(
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tags (all must match)."),
    text: str = typer.Option("", "--text", help="Substring match across notes/prompt/tags."),
) -> None:
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    hits = reference_lib.search(tags=tag_list, text=text)
    if not hits:
        console.print("[dim]no matches[/]")
        return
    for e in hits:
        console.print(f"[bold]{e['id']}[/] {e['filename']}  tags=[{', '.join(e.get('tags', []))}]  {e.get('notes', '')[:80]}")


@refs_app.command("recent")
def refs_recent(n: int = typer.Option(10, "-n")) -> None:
    for e in reference_lib.recent(n):
        console.print(f"[bold]{e['id']}[/] {e['filename']}  {e.get('added_at', '')}  {e.get('notes', '')[:80]}")


if __name__ == "__main__":
    app()
