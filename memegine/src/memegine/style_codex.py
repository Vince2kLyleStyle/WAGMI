from __future__ import annotations

import datetime as dt
from pathlib import Path

from .config import settings


def read(path: Path = settings.codex_path) -> str:
    if not path.exists():
        return ""
    return path.read_text()


def append_entry(
    section: str,
    body: str,
    path: Path = settings.codex_path,
) -> None:
    """Append a timestamped note under the named section.

    Section is matched case-insensitively by the header line (## Section).
    If the section doesn't exist, it's created at the bottom of the file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    stamp = dt.date.today().isoformat()
    entry = f"- ({stamp}) {body.strip()}"

    header = f"## {section}"
    lines = existing.splitlines()
    target_idx = next(
        (i for i, ln in enumerate(lines) if ln.strip().lower() == header.lower()),
        None,
    )

    if target_idx is None:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        new = existing + f"\n{header}\n{entry}\n"
        path.write_text(new)
        return

    insert_at = len(lines)
    for i in range(target_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            insert_at = i
            break
    lines.insert(insert_at, entry)
    path.write_text("\n".join(lines) + ("\n" if not existing.endswith("\n") else ""))


def log_winner(prompt: str, why: str, path: Path = settings.codex_path) -> None:
    append_entry("Proven Prompt Patterns", f'"{prompt}" — {why}', path)


def log_flop(what: str, why: str, path: Path = settings.codex_path) -> None:
    append_entry("Kill List", f"{what} — {why}", path)
