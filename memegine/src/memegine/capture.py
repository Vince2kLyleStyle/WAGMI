"""Idea queue — rough thoughts saved for later conversion to full briefs.

Workflow:
  memegine capture "rough thought"
  memegine capture list
  memegine capture convert <id> -f <format>    # turn into full brief

Backed by data/logs/captures.jsonl (append-only).
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import settings


@dataclass
class Capture:
    id: str
    created_at: str
    intent: str
    status: str = "pending"   # "pending" | "consumed" | "discarded"
    consumed_into_brief_id: str | None = None


def _path(logs_dir: Path | None = None) -> Path:
    base = Path(logs_dir) if logs_dir else settings.logs_dir
    base.mkdir(parents=True, exist_ok=True)
    return base / "captures.jsonl"


def _rewrite(entries: list[Capture], logs_dir: Path | None = None) -> None:
    p = _path(logs_dir)
    with p.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")


def _read_all(logs_dir: Path | None = None) -> list[Capture]:
    p = _path(logs_dir)
    if not p.exists():
        return []
    out: list[Capture] = []
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(Capture(**rec))
    return out


def add(intent: str, logs_dir: Path | None = None) -> Capture:
    """Append a new capture."""
    p = _path(logs_dir)
    c = Capture(
        id=uuid.uuid4().hex[:8],
        created_at=dt.datetime.utcnow().isoformat() + "Z",
        intent=intent.strip(),
    )
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    return c


def list_pending(logs_dir: Path | None = None) -> list[Capture]:
    return [c for c in _read_all(logs_dir) if c.status == "pending"]


def list_all(logs_dir: Path | None = None) -> list[Capture]:
    return _read_all(logs_dir)


def mark_consumed(capture_id: str, brief_id: str, logs_dir: Path | None = None) -> Capture | None:
    entries = _read_all(logs_dir)
    found: Capture | None = None
    for c in entries:
        if c.id == capture_id:
            c.status = "consumed"
            c.consumed_into_brief_id = brief_id
            found = c
            break
    if found is None:
        return None
    _rewrite(entries, logs_dir)
    return found


def discard(capture_id: str, logs_dir: Path | None = None) -> Capture | None:
    entries = _read_all(logs_dir)
    found: Capture | None = None
    for c in entries:
        if c.id == capture_id:
            c.status = "discarded"
            found = c
            break
    if found is None:
        return None
    _rewrite(entries, logs_dir)
    return found


def find(capture_id: str, logs_dir: Path | None = None) -> Capture | None:
    for c in _read_all(logs_dir):
        if c.id == capture_id or c.id.startswith(capture_id):
            return c
    return None


__all__ = [
    "Capture",
    "add",
    "list_pending",
    "list_all",
    "mark_consumed",
    "discard",
    "find",
]
