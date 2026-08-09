from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .config import settings


INDEX_PATH = settings.references_dir / "index.json"


@dataclass
class ReferenceEntry:
    id: str
    filename: str
    added_at: str
    tags: list[str] = field(default_factory=list)
    source: str = ""  # "grok", "shot", "web", etc.
    prompt: str = ""  # the prompt that produced it, if known
    notes: str = ""   # why it's a keeper


def _load_index(path: Path = INDEX_PATH) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _save_index(entries: list[dict], path: Path = INDEX_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2))


def add(
    image_path: Path,
    tags: list[str] | None = None,
    source: str = "",
    prompt: str = "",
    notes: str = "",
) -> ReferenceEntry:
    """Copy an image into the reference library and record it in the index."""
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(image_path)

    settings.references_dir.mkdir(parents=True, exist_ok=True)
    ref_id = hashlib.sha256(image_path.read_bytes()).hexdigest()[:12]
    dest = settings.references_dir / f"{ref_id}{image_path.suffix.lower()}"
    if not dest.exists():
        shutil.copy2(image_path, dest)

    entry = ReferenceEntry(
        id=ref_id,
        filename=dest.name,
        added_at=datetime.utcnow().isoformat() + "Z",
        tags=tags or [],
        source=source,
        prompt=prompt,
        notes=notes,
    )
    entries = _load_index()
    entries = [e for e in entries if e.get("id") != ref_id]
    entries.append(asdict(entry))
    _save_index(entries)
    return entry


def search(tags: list[str] | None = None, text: str = "") -> list[dict]:
    entries = _load_index()
    out = entries
    if tags:
        tag_set = {t.lower() for t in tags}
        out = [e for e in out if tag_set.issubset({t.lower() for t in e.get("tags", [])})]
    if text:
        needle = text.lower()
        out = [
            e for e in out
            if needle in e.get("notes", "").lower()
            or needle in e.get("prompt", "").lower()
            or needle in " ".join(e.get("tags", [])).lower()
        ]
    return out


def recent(n: int = 10) -> list[dict]:
    entries = _load_index()
    entries.sort(key=lambda e: e.get("added_at", ""), reverse=True)
    return entries[:n]


def reference_notes_for_prompt(tags: list[str] | None = None, limit: int = 5) -> str:
    """Return a short string block summarizing matching references, to feed into
    the prompt engine's user message.
    """
    hits = search(tags=tags) if tags else recent(limit)
    hits = hits[:limit]
    if not hits:
        return ""
    lines = ["Past winners from reference library:"]
    for e in hits:
        tag_str = ", ".join(e.get("tags", []))
        lines.append(f"- {e['filename']} [{tag_str}] — {e.get('notes') or e.get('prompt', '')[:80]}")
    return "\n".join(lines)
