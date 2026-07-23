"""Parse the project's ODB naming convention without opening an ODB."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_SAMPLE_RE = re.compile(
    r"^(?P<sample>GJA-(?P<gja_index>\d+)|D(?:800|1000|1200|1400(?:_17)?|1600))",
    re.IGNORECASE,
)
_LOAD_RE = re.compile(
    r"(?:^|[-_])(?P<axis>[UV])(?P<value>\d+(?:\.\d+)?)D(?=$|[-_])",
    re.IGNORECASE,
)
_REBAR_RE = re.compile(r"(?:^|[-_])R(?=$|[-_])", re.IGNORECASE)
_PARAMETER_RE = re.compile(r"(?:^|[-_])(?P<name>miu\d+)(?=$|[-_])", re.IGNORECASE)


def natural_sort_key(value: str) -> tuple[tuple[int, object], ...]:
    """Sort embedded decimal numbers numerically instead of lexicographically."""

    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", str(value))
        if part
    )


@dataclass(frozen=True)
class OdbNameInfo:
    stem: str
    sample_id: str
    family: str
    scheme: str
    reinforced: bool
    condition: str
    parameter_tags: tuple[str, ...]
    is_old: bool
    load_direction: str | None
    up_displacement_mm: float | None
    lateral_displacement_mm: float | None
    rebar_diameter_mm: float | None


def _display_number(value: str) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def _diameter(sample_id: str, gja_index: str | None) -> float | None:
    if gja_index is not None:
        index = int(gja_index)
        if 1 <= index <= 3:
            return 22.0
        if 4 <= index <= 17:
            return 28.0
        if 18 <= index <= 32:
            return 32.0
        return None
    return {
        "D800": 22.0,
        "D1000": 28.0,
        "D1200": 32.0,
        "D1400": 32.0,
        "D1400_17": 32.0,
        "D1600": 32.0,
    }.get(sample_id.upper())


def parse_odb_name(value: str | Path) -> OdbNameInfo:
    stem = Path(value).stem
    sample_match = _SAMPLE_RE.match(stem)
    sample_id = sample_match.group("sample") if sample_match else ""
    gja_index = sample_match.group("gja_index") if sample_match else None
    family = "GJA" if gja_index is not None else ("D" if sample_id.upper().startswith("D") else "")
    scheme = "A方案-钢筋混凝土" if family == "GJA" else "直径系列模型" if family == "D" else "未识别"

    load_tokens: list[str] = []
    up: float | None = None
    lateral: float | None = None
    for match in _LOAD_RE.finditer(stem):
        axis = match.group("axis").upper()
        number_text = _display_number(match.group("value"))
        load_tokens.append(f"{axis}{number_text}D")
        if axis == "U":
            up = float(match.group("value"))
        else:
            lateral = float(match.group("value"))

    if up is not None and lateral is not None:
        direction = "1+3"
    elif up is not None:
        direction = "3"
    elif lateral is not None:
        direction = "1"
    else:
        direction = None

    parameters = tuple(match.group("name").lower() for match in _PARAMETER_RE.finditer(stem))
    return OdbNameInfo(
        stem=stem,
        sample_id=sample_id,
        family=family,
        scheme=scheme,
        reinforced=bool(_REBAR_RE.search(stem)),
        condition="_".join(load_tokens),
        parameter_tags=parameters,
        is_old=bool(re.search(r"(?:^|[-_])old(?=$|[-_])", stem, re.IGNORECASE)),
        load_direction=direction,
        up_displacement_mm=up,
        lateral_displacement_mm=lateral,
        rebar_diameter_mm=_diameter(sample_id, gja_index),
    )
