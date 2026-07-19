"""Track pipeline stage completion (download/parse/upload/...) per quarter and per table.

Used across download.py, parse.py, and future upload/purge stages so each can
check what's already been done -- even after the underlying raw zip or
Parquet file has been deleted.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MANIFEST_PATH = Path("data/manifest.json")


def _load_manifest(manifest_path: Path) -> dict:
    """Load the manifest, or {} if it doesn't exist yet."""
    if manifest_path.exists():
        with open(manifest_path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logger.error(f"Manifest at {manifest_path} is corrupted or not valid JSON.")
                raise
    return {}


def _save_manifest(manifest: dict, manifest_path: Path) -> None:
    """Write the manifest to disk atomically (tmp file + replace)."""
    tmp_path = Path(f"{manifest_path}.tmp")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(tmp_path, "w") as f:
            json.dump(manifest, f, indent=2)
    except Exception:
        logger.error(f"Failed to write to tmp file {manifest_path}.tmp")
        raise
    tmp_path.replace(manifest_path)
    logger.info(f"{manifest_path} saved to manifest log.")


def mark_stage(
    quarter: str,
    stage: str,
    table: str | None = None,
    manifest_path: Path = MANIFEST_PATH,
) -> None:
    """Record that `quarter` (or `table` within it) has completed `stage`, with a UTC timestamp."""
    manifest = _load_manifest(manifest_path)
    entry = manifest.setdefault(quarter, {})
    if table:
        entry = entry.setdefault("tables", {}).setdefault(table, {})
    entry[stage] = datetime.now(timezone.utc).isoformat()
    _save_manifest(manifest, manifest_path)


def has_stage(
    quarter: str,
    stage: str,
    table: str | None = None,
    manifest_path: Path = MANIFEST_PATH,
) -> bool:
    """Check the manifest for whether `quarter` (or `table` within it) has completed `stage`."""
    manifest = _load_manifest(manifest_path)
    entry = manifest.get(quarter, {})
    if table:
        entry = entry.get("tables", {}).get(table, {})
    return stage in entry
