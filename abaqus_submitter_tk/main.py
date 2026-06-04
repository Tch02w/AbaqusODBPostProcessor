"""Entry point for the legacy Tk/CustomTkinter frontend."""

from __future__ import annotations


def main() -> None:
    """Start the legacy Tk application.

    The old script builds the UI at module import time, so the import is kept
    inside this function. This makes smoke imports safe while preserving the
    original runtime behavior.
    """
    from . import _legacy_app  # noqa: F401


if __name__ == "__main__":
    main()
