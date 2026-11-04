"""
Shared repair-routing helpers (research/reporting only). Identical file in
both repos - the per-repo CLI command (quant.pipeline
cmd_godel_news_repair_routing) supplies the actual load/save I/O for that
repo's own store; this module only classifies and describes the change.

One-time (but idempotent) repair of BTC/SPY association routing on
EXISTING captured news. Never touches raw record content (headline,
detail_text, hash, timestamps, ...) - only computes what the
association/association_confidence/association_reason fields SHOULD be,
using capture metadata first (requested_asset/selected_group, when a row
has it) and content classification second (news.asset_routing).
Most historical rows predate group-switching and have no capture
metadata, so they fall through to content-only classification entirely.

Idempotent by construction: repairing an already-repaired row reclassifies
to the SAME association (deterministic classifier, same inputs), so a
second --apply run produces action='no_change' for every row and rewrites
byte-identical data.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd

from news.asset_routing import classify_association


def backup_artifacts(paths: List[Path], backup_root: Path) -> Path:
    """Copy every existing path (file or dir) into a fresh timestamped
    backup folder before any --apply write. Never overwrites a prior
    backup - each call gets its own folder. Returns the backup folder."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_root = backup_root / f"repair_routing_backup_{stamp}"
    dest_root.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if not p.exists():
            continue
        target = dest_root / p.name
        if p.is_dir():
            shutil.copytree(p, target, dirs_exist_ok=True)
        else:
            shutil.copy2(p, target)
    return dest_root


def repair_row(row_id: str, headline: str, detail_text: str, ticker: str,
              selected_group: Optional[str], old_association: str) -> dict:
    """
    Classify ONE historical row: capture metadata first (if selected_group
    is present - i.e. this row was captured after group-switching existed
    and verified), content second. Returns an audit row describing the
    change; never mutates anything itself.
    """
    verified = bool(selected_group)
    new = classify_association(headline=headline, detail_text=detail_text,
                               ticker=ticker, selected_group=selected_group,
                               group_verified=verified)
    old = old_association or ""
    if new["association"] == old:
        action = "no_change"
    elif not old:
        action = "initial_classification"
    else:
        action = "reclassified"
    return {
        "row_id": row_id,
        "old_association": old,
        "new_association": new["association"],
        "confidence": new["confidence"],
        "reason": new["reason"],
        "capture_metadata_used": verified,
        "action": action,
    }


def write_audit_csv(audit_rows: List[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(audit_rows).to_csv(path, index=False)
    return path
