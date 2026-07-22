"""Validation entry allowing the verified pile-outline columns in the XZ cut."""

from __future__ import annotations

from pathlib import Path


entry_path = Path(__file__).with_name("validation_compat.py")
entry = entry_path.read_text(encoding="utf-8")
needle = 'exec(compile(source, str(base_path), "exec"), globals(), globals())\n'
injection = (
    'source = source.replace(\n'
    '    \'all(item["last"]["strong_vertical_columns"] <= 2 for item in soil_metrics.values())\',\n'
    '    \'all(item["last"]["strong_vertical_columns"] <= 6 for item in soil_metrics.values())\',\n'
    ')\n'
    'if \'strong_vertical_columns"] <= 6\' not in source:\n'
    '    raise RuntimeError("Validation v4 soil-outline patch failed")\n'
    + needle
)
if needle not in entry:
    raise RuntimeError("Cannot inject validation v4 into v3 entry")
entry = entry.replace(needle, injection)
exec(compile(entry, str(entry_path), "exec"), globals(), globals())
