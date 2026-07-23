from __future__ import annotations

import tempfile
from pathlib import Path

from abaqus_odb_postprocessor.config import abaqus_script
from abaqus_odb_postprocessor.paths import (
    batch_temp_dir,
    result_root_for_odb,
    scan_cache_dir,
)


def test_runtime_scripts_are_single_current_entries() -> None:
    for name in (
        "scan_odb.py",
        "odb_compatibility.py",
        "scan_legend_worker.py",
        "extract_job_worker.py",
        "render_group_contours.py",
    ):
        assert abaqus_script(name).is_file()


def test_cache_is_persistent_under_selected_odb_root(tmp_path: Path) -> None:
    cache = scan_cache_dir(tmp_path).resolve()
    assert cache == tmp_path.resolve() / "cache"


def test_batch_scratch_is_in_system_temp() -> None:
    scratch = batch_temp_dir("test-batch").resolve()
    assert scratch.is_relative_to(Path(tempfile.gettempdir()).resolve())


def test_results_are_next_to_odb(tmp_path: Path) -> None:
    odb = tmp_path / "case.odb"
    assert result_root_for_odb(odb) == tmp_path / "AbaqusODBPostProcessor_Results"


def test_contours_use_configurable_large_viewport() -> None:
    script_root = Path(__file__).parents[1] / "src" / "abaqus_odb_postprocessor"
    scripts = (
        script_root / "abaqus_scripts" / "extract_job.py",
        script_root / "abaqus_scripts" / "render_group_contours_core.py",
    )
    for script in scripts:
        source = script.read_text(encoding="utf-8")
        assert "(396.0, 264.0)" in source
        assert "imageSize=image_size_setting" in source
        assert "image_size_unit" in source
        assert "SIZE_ON_SCREEN" in source
        assert "imageSize=(1600, 1200)" not in source
    assert "imageSize=(800, 600)" not in source

    pipeline = (
        script_root / "abaqus_scripts" / "extract_job_pipeline.py"
    ).read_text(encoding="utf-8")
    compatibility = (
        script_root / "abaqus_scripts" / "extract_job_compatibility.py"
    ).read_text(encoding="utf-8")
    assert "imageSize=(1600, 1200)" not in pipeline
    assert "imageSize=image_size_setting" in pipeline
    assert "imageSize=image_size_setting" in compatibility


def test_frame_selection_preserves_history_and_full_gif_timeline() -> None:
    script_root = Path(__file__).parents[1] / "src" / "abaqus_odb_postprocessor"
    frame_compatibility = (
        script_root / "abaqus_scripts" / "extract_job_frame_compatibility.py"
    ).read_text(encoding="utf-8")
    group_renderer = (
        script_root / "abaqus_scripts" / "render_group_contours_core.py"
    ).read_text(encoding="utf-8")

    assert "full_timeline = list(timeline)" in frame_compatibility
    assert "selected_timeline_alignment.csv" in frame_compatibility
    assert "timeline = selected_timeline" in frame_compatibility
    assert "render_timeline = full_timeline" in frame_compatibility
    assert '"load_timehistory_points": len(full_timeline)' in frame_compatibility
    assert "selected_timeline_path" in group_renderer
    assert "render_timeline = full_timeline" in group_renderer

    start_marker = "injection = r'''"
    start = frame_compatibility.index(start_marker) + len(start_marker)
    end = frame_compatibility.index(
        "'''\nloader = loader.replace", start
    )
    injection = frame_compatibility[start:end]
    generated = {
        "source": (
            script_root / "abaqus_scripts" / "extract_job.py"
        ).read_text(encoding="utf-8")
    }
    exec(injection, generated, generated)
    patched = generated["source"]
    assert patched.index('for item in timeline:\n    frame = item["frame"]') < patched.index(
        "timeline = selected_timeline"
    )
    assert 'os.path.join(data_dir, "load_point_raw.csv")' in patched
    assert 'os.path.join(data_dir, "selected_timeline_alignment.csv")' in patched
    assert "render_timeline = full_timeline" in patched


def test_animation_and_static_contours_use_fixed_scoped_legends() -> None:
    script_root = Path(__file__).parents[1] / "src" / "abaqus_odb_postprocessor"
    extractor = (
        script_root / "abaqus_scripts" / "extract_job.py"
    ).read_text(encoding="utf-8")
    group_renderer = (
        script_root / "abaqus_scripts" / "render_group_contours_core.py"
    ).read_text(encoding="utf-8")

    for source in (extractor, group_renderer):
        assert "def set_animation_limits(spec):" in source
        assert "def set_static_limits(spec):" in source
        assert "maxAutoCompute=ON" in source
        assert "set_animation_limits(spec)" in source
        assert "set_static_limits(spec)" in source
        assert 'static_base = os.path.join(contour_dir, spec["name"] + "_LAST")' in source
        assert '"animation_legend_mode": "odb_full_timeline_fixed"' in source
        assert "animation_legend_ranges" in source
        assert (
            '"static_contour_legend_mode": '
            '"comparison_group_fixed_selected_frames"'
        ) in source
        assert "shutil.copyfile(written[-1]" not in source

    animation_call = group_renderer.index(
        "set_animation_limits(spec)",
        group_renderer.index("for animation_index, item in enumerate(render_timeline):"),
    )
    frame_call = group_renderer.index(
        "viewport.odbDisplay.setFrame(step=step_index, frame=frame_index)",
        group_renderer.index("for animation_index, item in enumerate(render_timeline):"),
    )
    print_call = group_renderer.index(
        "session.printToFile(fileName=base",
        group_renderer.index("for animation_index, item in enumerate(render_timeline):"),
    )
    assert frame_call < animation_call < print_call


def test_worker_extracts_root_key_and_pile_contact_histories() -> None:
    worker = (
        Path(__file__).parents[1]
        / "src"
        / "abaqus_odb_postprocessor"
        / "abaqus_scripts"
        / "extract_job.py"
    ).read_text(encoding="utf-8")
    scanner = (
        Path(__file__).parents[1]
        / "src"
        / "abaqus_odb_postprocessor"
        / "abaqus_scripts"
        / "scan_odb_core.py"
    ).read_text(encoding="utf-8")

    assert 're.search(r"KEY(?:[_\\- ]+\\d+)+", upper)' in worker
    assert '"CFN1", "CFN2", "CFN3", "CFNM"' in worker
    assert '"CFS1", "CFS2", "CFS3", "CFSM"' in worker
    assert '"contact_history_raw.csv"' in worker
    assert '"contact_history_sources.json"' in worker
    assert 'if "KEY" in upper:' in worker
    assert "history_output_details" in scanner
    assert "output_previews" in scanner
    assert "if data is None:" in scanner


def test_scan_worker_accepts_nested_selected_odb_paths() -> None:
    script = (
        Path(__file__).parents[1]
        / "src"
        / "abaqus_odb_postprocessor"
        / "abaqus_scripts"
        / "scan_odb.py"
    ).read_text(encoding="utf-8")
    assert "relative = os.path.relpath(path, folder)" in script
    assert "not relative.startswith(os.pardir + os.sep)" in script
