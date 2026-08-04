"""Persistent ODB scan and extraction cache helpers.

Cache identity is based on sampled file content rather than the file name.
This lets a renamed or copied ODB reuse completed work without hashing the
entire (potentially very large) file.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .config import save_json


CACHE_SCHEMA_VERSION = 1
SAMPLE_SIZE = 64 * 1024
CACHE_KINDS = ("compatibility", "initial", "prescan", "numeric")


def quick_odb_fingerprint(path: Path, sample_size: int = SAMPLE_SIZE) -> str:
    """Hash file size plus fixed head/middle/tail samples with SHA-256."""

    source = path.resolve()
    before = source.stat()
    size = before.st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    offsets = (0, max((size - sample_size) // 2, 0), max(size - sample_size, 0))
    with source.open("rb") as stream:
        for offset in offsets:
            stream.seek(offset)
            block = stream.read(sample_size)
            digest.update(offset.to_bytes(8, "big", signed=False))
            digest.update(len(block).to_bytes(8, "big", signed=False))
            digest.update(block)
    after = source.stat()
    if (
        after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise RuntimeError(f"ODB 文件在生成缓存指纹时发生变化：{source}")
    return digest.hexdigest()


def stable_config_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def odb_file_metadata(path: Path) -> dict[str, Any]:
    source = path.resolve()
    stat = source.stat()
    return {
        "odb_path": str(source),
        "file_size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def cache_entry_dir(
    cache_root: Path,
    kind: str,
    content_fingerprint: str,
    config_hash: str = "",
) -> Path:
    path = cache_root / kind / content_fingerprint
    if config_hash:
        path /= config_hash
    return path


def invalidate_content_fingerprint(
    cache_root: Path,
    content_fingerprint: str,
) -> list[Path]:
    """Remove every known cache layer associated with one ODB fingerprint."""

    removed: list[Path] = []
    root = cache_root.resolve()
    for kind in CACHE_KINDS:
        target = root / kind / content_fingerprint
        if not target.is_dir():
            continue
        shutil.rmtree(target)
        removed.append(target)
    return removed


def cache_metadata(
    *,
    kind: str,
    odb_path: Path,
    content_fingerprint: str,
    abaqus_version: str,
    config_hash: str,
    config_snapshot: Any,
) -> dict[str, Any]:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "kind": kind,
        "content_fingerprint": content_fingerprint,
        "abaqus_version": str(abaqus_version),
        "config_hash": config_hash,
        "config_snapshot": config_snapshot,
        **odb_file_metadata(odb_path),
    }


def metadata_matches(
    metadata: dict[str, Any],
    *,
    kind: str,
    content_fingerprint: str,
    abaqus_version: str,
    config_hash: str,
) -> bool:
    """Validate identity fields; mtime/path are intentionally informational."""

    return (
        int(metadata.get("schema_version", -1)) == CACHE_SCHEMA_VERSION
        and metadata.get("kind") == kind
        and metadata.get("content_fingerprint") == content_fingerprint
        and str(metadata.get("abaqus_version", "")) == str(abaqus_version)
        and metadata.get("config_hash", "") == config_hash
    )


def load_json_cache(
    cache_root: Path,
    kind: str,
    content_fingerprint: str,
    abaqus_version: str,
    config_hash: str = "",
) -> dict[str, Any] | None:
    entry = cache_entry_dir(cache_root, kind, content_fingerprint, config_hash)
    metadata_path = entry / "cache_metadata.json"
    payload_path = entry / "payload.json"
    if not metadata_path.is_file() or not payload_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not metadata_matches(
            metadata,
            kind=kind,
            content_fingerprint=content_fingerprint,
            abaqus_version=abaqus_version,
            config_hash=config_hash,
        ):
            return None
        return json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def save_json_cache(
    cache_root: Path,
    kind: str,
    odb_path: Path,
    content_fingerprint: str,
    abaqus_version: str,
    payload: dict[str, Any],
    *,
    config_hash: str = "",
    config_snapshot: Any = None,
) -> Path:
    entry = cache_entry_dir(cache_root, kind, content_fingerprint, config_hash)
    entry.mkdir(parents=True, exist_ok=True)
    save_json(entry / "payload.json", payload)
    save_json(
        entry / "cache_metadata.json",
        cache_metadata(
            kind=kind,
            odb_path=odb_path,
            content_fingerprint=content_fingerprint,
            abaqus_version=abaqus_version,
            config_hash=config_hash,
            config_snapshot=config_snapshot,
        ),
    )
    return entry


def numeric_config_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the frozen fields that can affect extracted numeric results."""

    keys = (
        "start_step",
        "end_step",
        "frame_mode",
        "manual_sequence_expression",
        "selected_sequence_indices",
        "load_direction",
        "load_set",
        "pile_type",
        "pile_display_set",
        "pile_concrete_set",
        "pile_steel_set",
        "soil_set",
        "rebar_set",
        "longitudinal_material",
        "rebar_diameter_mm",
        "prefracture_sequence_index",
        "full_timehistory_freebody",
    )
    settings = payload.get("settings", {})
    numeric_setting_keys = (
        "axial_cut_count",
        "pile_head_above_ground_mm",
        "longitudinal_orientation_threshold",
        "damage_threshold",
    )
    return {
        **{key: payload.get(key) for key in keys},
        "settings": {
            key: settings.get(key) for key in numeric_setting_keys
        },
    }


def prescan_config_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Return inputs used by the field-range/damage prescan."""

    keys = (
        "start_step",
        "end_step",
        "pile_display_set",
        "pile_concrete_set",
        "pile_steel_set",
        "soil_set",
        "rebar_set",
        "longitudinal_material",
    )
    settings = payload.get("settings", {})
    prescan_setting_keys = (
        "longitudinal_orientation_threshold",
        "soil_section_coordinate",
        "damage_threshold",
        "damage_angular_coverage",
    )
    return {
        **{key: payload.get(key) for key in keys},
        "settings": {
            key: settings.get(key) for key in prescan_setting_keys
        },
    }


def numeric_cache_is_valid(
    entry: Path,
    *,
    content_fingerprint: str,
    abaqus_version: str,
    config_hash: str,
) -> bool:
    metadata_path = entry / "cache_metadata.json"
    required = (
        entry / "data" / "timeline_alignment.csv",
        entry / "rebar",
        entry / "freebody",
    )
    if not metadata_path.is_file() or not all(path.exists() for path in required):
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return metadata_matches(
        metadata,
        kind="numeric",
        content_fingerprint=content_fingerprint,
        abaqus_version=abaqus_version,
        config_hash=config_hash,
    )


def write_numeric_cache_metadata(
    entry: Path,
    *,
    odb_path: Path,
    content_fingerprint: str,
    abaqus_version: str,
    config_hash: str,
    config_snapshot: dict[str, Any],
) -> None:
    entry.mkdir(parents=True, exist_ok=True)
    save_json(
        entry / "cache_metadata.json",
        cache_metadata(
            kind="numeric",
            odb_path=odb_path,
            content_fingerprint=content_fingerprint,
            abaqus_version=abaqus_version,
            config_hash=config_hash,
            config_snapshot=config_snapshot,
        ),
    )


def abaqus_cache_version(defaults: dict[str, Any]) -> str:
    """Keep release and executable identity separate from the content key."""

    return "{0}|{1}".format(
        defaults.get("local_abaqus_release", ""),
        os.path.normcase(str(defaults.get("abaqus_command", ""))),
    )
