"""Final validation entry including the verified explicit +Y soil camera."""

from __future__ import annotations

from pathlib import Path


base_path = Path(__file__).with_name("validation_core.py")
source = base_path.read_text(encoding="utf-8")
source = source.replace(
    '    gif_counts = {}\n'
    '    for path in sorted((root / "animations").glob("*.gif")):\n'
    '        with Image.open(path) as image:\n'
    '            gif_counts[path.stem] = int(getattr(image, "n_frames", 1))\n',
    '    gif_counts = {}\n'
    '    gif_duration_ms = {}\n'
    '    for path in sorted((root / "animations").glob("*.gif")):\n'
    '        with Image.open(path) as image:\n'
    '            frame_total = int(getattr(image, "n_frames", 1))\n'
    '            gif_counts[path.stem] = frame_total\n'
    '            duration = 0\n'
    '            for frame_index in range(frame_total):\n'
    '                image.seek(frame_index)\n'
    '                duration += int(image.info.get("duration", 0))\n'
    '            gif_duration_ms[path.stem] = duration\n',
)
source = source.replace(
    '        "gif_frame_count_by_sequence": gif_counts,\n',
    '        "gif_frame_count_by_sequence": gif_counts,\n'
    '        "gif_total_duration_ms_by_sequence": gif_duration_ms,\n',
)
source = source.replace(
    '            "soil_preset": "Bottom",\n',
    '            "soil_preset": metadata.get("soil_view_preset", "Explicit XZ"),\n'
    '            "view_vector": metadata.get("soil_view_vector", [0.0, 1.0, 0.0]),\n'
    '            "camera_up_vector": metadata.get("soil_camera_up_vector", [0.0, 0.0, 1.0]),\n',
)
source = source.replace(
    '"timeline": csv_rows(root / "timeline.csv"),\n'
    '            "load_raw": csv_rows(root / "load_point" / "load_displacement_raw_time_aligned.csv"),',
    '"timeline": csv_rows(root / "data" / "timeline_alignment.csv"),\n'
    '            "load_raw": csv_rows(root / "data" / "load_point_raw.csv"),',
)
source = source.replace(
    '        len(gif_counts) == 11 and all(value == expected_frames for value in gif_counts.values()),\n',
    '        len(gif_counts) == 11 and all(value > 0 for value in gif_counts.values())\n'
    '        and all(value == expected_frames * 200 for value in gif_duration_ms.values()),\n',
)
source = source.replace(
    '        metadata["soil_set"] == "SET-SOIL_CUT",\n',
    '        metadata.get("soil_view_vector") == [0.0, 1.0, 0.0],\n'
    '        metadata.get("soil_camera_up_vector") == [0.0, 0.0, 1.0],\n'
    '        metadata["soil_set"] == "SET-SOIL_CUT",\n',
)
for marker in (
    'gif_total_duration_ms_by_sequence',
    'root / "data" / "timeline_alignment.csv"',
    'root / "data" / "load_point_raw.csv"',
    'expected_frames * 200',
    'metadata.get("soil_view_vector") == [0.0, 1.0, 0.0]',
    '"camera_up_vector": metadata.get',
):
    if marker not in source:
        raise RuntimeError(f"Validation v3 patch failed: {marker}")
exec(compile(source, str(base_path), "exec"), globals(), globals())
