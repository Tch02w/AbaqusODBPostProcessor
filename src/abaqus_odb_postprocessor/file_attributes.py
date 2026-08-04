"""Windows file-attribute helpers for internal application artifacts."""

from __future__ import annotations

import os
from pathlib import Path


INTERNAL_RESULT_JSON_FILENAMES = frozenset(
    {
        "comparison_group_legends.json",
        "frame_catalog_and_ranges.json",
        "host_postprocess_manifest.json",
        "job_config.json",
        "metadata.json",
        "numeric_postprocess_manifest.json",
        "render_postprocess_manifest.json",
        "_odb_data_manifest.json",
        "_group_member_manifest.json",
    }
)


def ensure_windows_hidden(path: Path | str) -> None:
    """Add the Windows hidden attribute while preserving other attributes."""

    if os.name != "nt":
        return

    import ctypes
    from ctypes import wintypes

    target = Path(path).resolve()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_attributes = kernel32.GetFileAttributesW
    get_attributes.argtypes = [wintypes.LPCWSTR]
    get_attributes.restype = wintypes.DWORD
    set_attributes = kernel32.SetFileAttributesW
    set_attributes.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    set_attributes.restype = wintypes.BOOL

    hidden_attribute = 0x2
    invalid_attributes = 0xFFFFFFFF
    attributes = get_attributes(str(target))
    if attributes == invalid_attributes:
        error_code = ctypes.get_last_error()
        raise OSError(error_code, os.strerror(error_code), str(target))
    if attributes & hidden_attribute:
        return
    if not set_attributes(str(target), attributes | hidden_attribute):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, os.strerror(error_code), str(target))


def clear_windows_hidden(path: Path | str) -> None:
    """Remove only the hidden attribute so an internal file can be rewritten."""

    if os.name != "nt":
        return

    import ctypes
    from ctypes import wintypes

    target = Path(path).resolve()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_attributes = kernel32.GetFileAttributesW
    get_attributes.argtypes = [wintypes.LPCWSTR]
    get_attributes.restype = wintypes.DWORD
    set_attributes = kernel32.SetFileAttributesW
    set_attributes.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    set_attributes.restype = wintypes.BOOL

    hidden_attribute = 0x2
    invalid_attributes = 0xFFFFFFFF
    attributes = get_attributes(str(target))
    if attributes == invalid_attributes:
        error_code = ctypes.get_last_error()
        raise OSError(error_code, os.strerror(error_code), str(target))
    if not attributes & hidden_attribute:
        return
    visible_attributes = attributes & ~hidden_attribute
    if visible_attributes == 0:
        visible_attributes = 0x80
    if not set_attributes(str(target), visible_attributes):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, os.strerror(error_code), str(target))


def hide_internal_result_json_files(directory: Path | str) -> list[Path]:
    """Hide known internal JSON files directly inside a result directory."""

    root = Path(directory)
    hidden: list[Path] = []
    for name in INTERNAL_RESULT_JSON_FILENAMES:
        path = root / name
        if not path.is_file():
            continue
        ensure_windows_hidden(path)
        hidden.append(path)
    return hidden
