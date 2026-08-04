"""Optional Matplotlib plotting with a Pillow fallback for Python previews."""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw
lazy from matplotlib import pyplot as plt


@dataclass(frozen=True, slots=True)
class LineSeries:
    label: str
    x: Sequence[float]
    y: Sequence[float]
    width: float = 1.7


def _expanded_range(values: Sequence[float]) -> tuple[float, float]:
    lower = min(values)
    upper = max(values)
    if lower != upper:
        padding = (upper - lower) * 0.04
        return lower - padding, upper + padding
    padding = max(abs(lower) * 0.04, 1.0)
    return lower - padding, upper + padding


def _save_with_pillow(
    target: Path,
    series: Sequence[LineSeries],
    *,
    x_label: str,
    y_label: str,
    title: str,
    invert_y: bool,
    zero_x: bool,
    zero_y: bool,
) -> None:
    width, height = 1368, 1116
    left, top, right, bottom = 120, 75, 65, 125
    plot_width = width - left - right
    plot_height = height - top - bottom
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    all_x = [float(value) for item in series for value in item.x]
    all_y = [float(value) for item in series for value in item.y]
    x_min, x_max = _expanded_range(all_x or [0.0])
    y_min, y_max = _expanded_range(all_y or [0.0])

    def point(x_value: float, y_value: float) -> tuple[int, int]:
        x_ratio = (x_value - x_min) / (x_max - x_min)
        y_ratio = (y_value - y_min) / (y_max - y_min)
        x_pixel = left + round(x_ratio * plot_width)
        y_pixel = (
            top + round(y_ratio * plot_height)
            if invert_y
            else top + plot_height - round(y_ratio * plot_height)
        )
        return x_pixel, y_pixel

    for index in range(6):
        x = left + round(index * plot_width / 5)
        y = top + round(index * plot_height / 5)
        draw.line((x, top, x, top + plot_height), fill="#E5E7EB", width=1)
        draw.line((left, y, left + plot_width, y), fill="#E5E7EB", width=1)
    draw.rectangle(
        (left, top, left + plot_width, top + plot_height),
        outline="#111827",
        width=2,
    )
    if zero_x and x_min <= 0 <= x_max:
        x, _ = point(0.0, y_min)
        draw.line((x, top, x, top + plot_height), fill="#111827", width=2)
    if zero_y and y_min <= 0 <= y_max:
        _, y = point(x_min, 0.0)
        draw.line((left, y, left + plot_width, y), fill="#111827", width=2)

    colors = ("#2563EB", "#EA580C", "#059669", "#7C3AED", "#DC2626")
    for index, item in enumerate(series):
        points = [
            point(float(x_value), float(y_value))
            for x_value, y_value in zip(item.x, item.y)
        ]
        if len(points) >= 2:
            draw.line(
                points,
                fill=colors[index % len(colors)],
                width=max(2, round(item.width * 2)),
                joint="curve",
            )
        elif points:
            x, y = points[0]
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=colors[index % len(colors)])

    if title:
        draw.text((left, 25), title, fill="#111827")
    draw.text((left + plot_width // 2 - 100, height - 55), x_label, fill="#111827")
    draw.text((18, top), y_label, fill="#111827")
    legend_y = top + 14
    for index, item in enumerate(series):
        color = colors[index % len(colors)]
        draw.line((left + 15, legend_y + 6, left + 45, legend_y + 6), fill=color, width=4)
        draw.text((left + 53, legend_y), item.label, fill="#111827")
        legend_y += 23
    draw.text((left, height - 90), f"x: {x_min:.4g} to {x_max:.4g}", fill="#4B5563")
    draw.text((left + 240, height - 90), f"y: {y_min:.4g} to {y_max:.4g}", fill="#4B5563")
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, dpi=(180, 180))


def save_line_chart(
    target: Path,
    series: Sequence[LineSeries],
    *,
    x_label: str,
    y_label: str,
    title: str = "",
    invert_y: bool = False,
    zero_x: bool = False,
    zero_y: bool = False,
) -> Path:
    """Save a chart while keeping Matplotlib optional on preview Python builds."""

    target = Path(target)
    if importlib.util.find_spec("matplotlib") is None:
        _save_with_pillow(
            target,
            series,
            x_label=x_label,
            y_label=y_label,
            title=title,
            invert_y=invert_y,
            zero_x=zero_x,
            zero_y=zero_y,
        )
        return target

    figure, axes = plt.subplots(figsize=(7.6, 8.0), constrained_layout=True)
    for item in series:
        axes.plot(item.x, item.y, label=item.label, linewidth=item.width)
    if zero_x:
        axes.axvline(0.0, color="black", linewidth=0.8)
    if zero_y:
        axes.axhline(0.0, color="black", linewidth=0.8)
    if invert_y:
        axes.invert_yaxis()
    axes.grid(True, alpha=0.25)
    axes.set_xlabel(x_label)
    axes.set_ylabel(y_label)
    if title:
        axes.set_title(title)
    axes.legend()
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target, dpi=180)
    plt.close(figure)
    return target
