"""Fast filesystem-only ODB discovery separated from Abaqus inspection."""

from __future__ import annotations

from pathlib import Path

from .naming import natural_sort_key

EXCLUDED_ODB_DIRECTORIES = frozenset(
    {
        "AbaqusODBPostProcessor_Results",
        "_AbaqusODBPostProcessor_Results",
        ".git",
        ".venv",
    }
)


def discover_odb_paths(folder: Path) -> list[Path]:
    """Discover ODB files recursively while excluding generated results."""

    root = Path(folder)
    paths: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".odb":
            continue
        stem = path.stem.casefold()
        if stem.endswith("-old") or "-old-" in stem or "-upgrading-" in stem:
            continue
        relative_parts = path.relative_to(root).parts[:-1]
        if any(part in EXCLUDED_ODB_DIRECTORIES for part in relative_parts):
            continue
        paths.append(path)
    return sorted(
        paths,
        key=lambda path: natural_sort_key(str(path.relative_to(root))),
    )
