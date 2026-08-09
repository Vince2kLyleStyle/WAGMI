"""Brief archive — every assembled brief is saved to disk so you can replay,
audit, and mine patterns from what the Director wrote for you.

Format: one JSONL line per brief under data/logs/briefs-YYYY-MM-DD.jsonl.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings


@dataclass
class ArchivedBrief:
    id: str
    created_at: str
    kind: str  # "prompt" | "shots" | "copy" | "pipeline"
    format: str | None
    intent: str
    system: str
    user: str
    extra: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "kind": self.kind,
            "format": self.format,
            "intent": self.intent,
            "system": self.system,
            "user": self.user,
            "extra": self.extra,
        }


def _log_path_for_today(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()
    return base / f"briefs-{stamp}.jsonl"


def save(
    *,
    kind: str,
    intent: str,
    system: str,
    user: str,
    format_: str | None = None,
    extra: dict[str, Any] | None = None,
    logs_dir: Path | None = None,
) -> ArchivedBrief:
    base = Path(logs_dir) if logs_dir else settings.logs_dir
    path = _log_path_for_today(base)
    brief = ArchivedBrief(
        id=uuid.uuid4().hex[:12],
        created_at=dt.datetime.utcnow().isoformat() + "Z",
        kind=kind,
        format=format_,
        intent=intent.strip(),
        system=system,
        user=user,
        extra=extra or {},
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(brief.to_dict(), ensure_ascii=False) + "\n")
    return brief


def read_recent(n: int = 20, logs_dir: Path | None = None) -> list[dict]:
    base = Path(logs_dir) if logs_dir else settings.logs_dir
    if not base.exists():
        return []
    files = sorted(base.glob("briefs-*.jsonl"), reverse=True)
    out: list[dict] = []
    for f in files:
        for line in reversed(f.read_text().splitlines()):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(out) >= n:
                return out
    return out


def find(brief_id: str, logs_dir: Path | None = None) -> dict | None:
    base = Path(logs_dir) if logs_dir else settings.logs_dir
    if not base.exists():
        return None
    for f in sorted(base.glob("briefs-*.jsonl"), reverse=True):
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("id") == brief_id:
                return rec
    return None


def search(text: str, logs_dir: Path | None = None) -> list[dict]:
    base = Path(logs_dir) if logs_dir else settings.logs_dir
    if not base.exists():
        return []
    needle = text.lower()
    out = []
    for f in sorted(base.glob("briefs-*.jsonl"), reverse=True):
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            hay = (rec.get("intent", "") + " " + rec.get("user", "")).lower()
            if needle in hay:
                out.append(rec)
    return out
