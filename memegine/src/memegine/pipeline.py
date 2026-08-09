"""Pipeline — bundle brief + shots (if video) + copy into a single session.

One operator intent -> one folder on disk containing every brief the Director
needs to produce for a complete piece, plus a concat.txt + README inside the
folder describing the order of operations.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import archive, copy_writer, prompt_engine, shot_list
from .config import settings


@dataclass
class PipelineBundle:
    id: str
    created_at: str
    intent: str
    kind: str  # "image" | "video"
    format_slug: str | None
    briefs: dict[str, dict] = field(default_factory=dict)  # key -> {"system":..., "user":...}
    folder: str = ""


def _bundle_folder(root: Path, bundle_id: str, intent: str) -> Path:
    stamp = dt.date.today().isoformat()
    slug = "".join(c if c.isalnum() or c in "-_ " else "" for c in intent).strip().replace(" ", "-")[:40]
    folder = root / f"{stamp}_{slug}_{bundle_id}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write_brief_file(folder: Path, name: str, system: str, user: str) -> Path:
    target = folder / f"{name}.md"
    content = (
        f"# {name} brief\n\n"
        "Paste both blocks into Claude Code (or claude.ai) as a single prompt.\n\n"
        "## SYSTEM\n\n```\n"
        + system
        + "\n```\n\n## USER\n\n```\n"
        + user
        + "\n```\n"
    )
    target.write_text(content)
    return target


def build(
    intent: str,
    *,
    kind: str,
    format_slug: str | None = None,
    include_copy: bool = True,
    outputs_dir: Path | None = None,
) -> PipelineBundle:
    """Assemble every brief needed for one piece. Persist to disk.

    kind="image": prompt + copy
    kind="video": shot_list + copy (shot_list itself covers per-shot still + motion prompts)
    """
    if kind not in ("image", "video"):
        raise ValueError(f"kind must be 'image' or 'video', got {kind!r}")

    base = Path(outputs_dir) if outputs_dir else settings.outputs_dir
    base.mkdir(parents=True, exist_ok=True)
    bid = uuid.uuid4().hex[:10]
    folder = _bundle_folder(base, bid, intent)

    briefs: dict[str, dict] = {}

    if kind == "image":
        if not format_slug:
            raise ValueError("format_slug is required for kind='image'")
        system, user = prompt_engine.assemble_offline_prompt(intent, format_slug)
        briefs["prompt"] = {"system": system, "user": user}
        _write_brief_file(folder, "01-prompt", system, user)
        archive.save(kind="prompt", intent=intent, system=system, user=user, format_=format_slug)

    if kind == "video":
        system, user = shot_list.assemble_offline_shot_list_prompt(intent)
        briefs["shots"] = {"system": system, "user": user}
        _write_brief_file(folder, "01-shots", system, user)
        archive.save(kind="shots", intent=intent, system=system, user=user)

    if include_copy:
        asset_kind = "image" if kind == "image" else "video"
        sys_c, user_c = copy_writer.assemble_offline_copy_prompt(intent, asset_kind)
        briefs["copy"] = {"system": sys_c, "user": user_c}
        _write_brief_file(folder, "02-copy", sys_c, user_c)
        archive.save(kind="copy", intent=intent, system=sys_c, user=user_c)

    # Write a session README so the folder is self-documenting.
    readme = folder / "README.md"
    readme.write_text(
        f"# Pipeline bundle — {intent}\n\n"
        f"- id: `{bid}`\n"
        f"- created: {dt.datetime.utcnow().isoformat()}Z\n"
        f"- kind: {kind}\n"
        f"- format: {format_slug or '(n/a)'}\n\n"
        "## Workflow\n\n"
        "1. Open each `.md` file in order.\n"
        "2. Paste its SYSTEM + USER blocks into Claude Code (this session) or claude.ai.\n"
        "3. Claude returns JSON; copy the `prompt` / `still_prompt` / `motion_prompt` / caption fields.\n"
        "4. Paste prompts into Grok Imagine / Nano Banana / Aurora to generate.\n"
        "5. When a piece lands, run:\n"
        "   - `memegine refs add <image> --tags ... --notes ...`\n"
        "   - `memegine codex winner '<prompt>' 'why'`\n"
    )

    bundle = PipelineBundle(
        id=bid,
        created_at=dt.datetime.utcnow().isoformat() + "Z",
        intent=intent,
        kind=kind,
        format_slug=format_slug,
        briefs=briefs,
        folder=str(folder),
    )
    (folder / "bundle.json").write_text(json.dumps(asdict(bundle), indent=2))
    return bundle
