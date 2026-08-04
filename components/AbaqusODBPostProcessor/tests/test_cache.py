from __future__ import annotations

import threading
from pathlib import Path

from abaqus_odb_postprocessor import runner as runner_module
from abaqus_odb_postprocessor.cache import (
    CACHE_SCHEMA_VERSION,
    cache_entry_dir,
    load_json_cache,
    numeric_config_snapshot,
    quick_odb_fingerprint,
    save_json_cache,
    stable_config_hash,
)


def test_quick_fingerprint_survives_rename_and_records_sampled_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "case-2.odb"
    source.write_bytes((b"head" * 20000) + (b"middle" * 20000) + (b"tail" * 20000))
    fingerprint = quick_odb_fingerprint(source)
    renamed = tmp_path / "case-20.odb"
    source.rename(renamed)
    assert quick_odb_fingerprint(renamed) == fingerprint

    payload = bytearray(renamed.read_bytes())
    payload[len(payload) // 2] ^= 0xFF
    renamed.write_bytes(payload)
    assert quick_odb_fingerprint(renamed) != fingerprint


def test_json_cache_ignores_path_and_mtime_but_checks_schema_and_config(
    tmp_path: Path,
) -> None:
    source = tmp_path / "A.odb"
    source.write_bytes(b"same-content")
    fingerprint = quick_odb_fingerprint(source)
    snapshot = {"start_step": "Load"}
    config_hash = stable_config_hash(snapshot)
    save_json_cache(
        tmp_path / "cache",
        "prescan",
        source,
        fingerprint,
        "2025|abaqus",
        {"value": 3},
        config_hash=config_hash,
        config_snapshot=snapshot,
    )
    renamed = tmp_path / "B.odb"
    source.rename(renamed)
    assert load_json_cache(
        tmp_path / "cache",
        "prescan",
        quick_odb_fingerprint(renamed),
        "2025|abaqus",
        config_hash,
    ) == {"value": 3}
    assert (
        load_json_cache(
            tmp_path / "cache",
            "prescan",
            fingerprint,
            "2025|abaqus",
            stable_config_hash({"start_step": "Other"}),
        )
        is None
    )
    assert CACHE_SCHEMA_VERSION >= 1


def test_initial_scan_cache_is_reused_after_odb_rename(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "odb"
    folder.mkdir()
    source = folder / "case-2.odb"
    source.write_bytes(b"odb-content")
    calls = 0

    def fake_run_process(arguments, _cwd, log=None, controller=None):
        nonlocal calls
        calls += 1
        selection_path = Path(arguments[arguments.index("--selection") + 1])
        output_path = Path(arguments[arguments.index("--output") + 1])
        selected = runner_module._load_json_report(selection_path)["paths"]
        runner_module.save_json(
            output_path,
            {
                "folder": str(folder),
                "odbs": [
                    {
                        "path": selected[0],
                        "steps": ["Load"],
                        "assembly_node_sets": [],
                        "assembly_element_sets": [],
                        "field_outputs": [],
                    }
                ],
            },
        )

    monkeypatch.setattr(runner_module, "run_process", fake_run_process)
    cache = folder / "cache"
    first = runner_module.scan_folder(
        "abaqus",
        folder,
        cache,
        selected_paths=[source],
        abaqus_version="2025|abaqus",
    )
    renamed = folder / "case-20.odb"
    source.rename(renamed)
    second = runner_module.scan_folder(
        "abaqus",
        folder,
        cache,
        selected_paths=[renamed],
        abaqus_version="2025|abaqus",
    )
    forced = runner_module.scan_folder(
        "abaqus",
        folder,
        cache,
        selected_paths=[renamed],
        force_rescan=True,
        abaqus_version="2025|abaqus",
    )
    assert calls == 2
    assert first["cache_hits"] == 0
    assert second["cache_hits"] == 1
    assert forced["cache_hits"] == 0
    assert second["odbs"][0]["path"] == str(renamed.resolve())


def test_compatibility_cache_reuses_stable_result_after_rename(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "odb"
    folder.mkdir()
    source = folder / "case-2.odb"
    source.write_bytes(b"compatible-odb")
    calls = 0

    def fake_run_process(arguments, _cwd, log=None, controller=None):
        nonlocal calls
        calls += 1
        request_path = Path(arguments[arguments.index("--request") + 1])
        output_path = Path(arguments[arguments.index("--output") + 1])
        request = runner_module._load_json_report(request_path)
        runner_module.save_json(
            output_path,
            {
                "mode": "check",
                "results": [
                    {
                        "path": path,
                        "status": "valid",
                        "message": "可读取",
                    }
                    for path in request["paths"]
                ],
            },
        )

    monkeypatch.setattr(runner_module, "run_process", fake_run_process)
    cache = folder / "cache"
    first = runner_module.check_odb_compatibility(
        "abaqus",
        [source],
        cache,
        abaqus_version="2025|abaqus",
    )
    renamed = folder / "case-20.odb"
    source.rename(renamed)
    second = runner_module.check_odb_compatibility(
        "abaqus",
        [renamed],
        cache,
        abaqus_version="2025|abaqus",
    )
    forced = runner_module.check_odb_compatibility(
        "abaqus",
        [renamed],
        cache,
        force_rescan=True,
        abaqus_version="2025|abaqus",
    )

    assert calls == 2
    assert first["cache_hits"] == 0
    assert second["cache_hits"] == 1
    assert forced["cache_hits"] == 0
    assert second["results"][0]["path"] == str(renamed.resolve())


def test_compatibility_cache_does_not_persist_transient_invalid_result(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "locked.odb"
    source.write_bytes(b"locked")
    calls = 0

    def fake_run_process(arguments, _cwd, log=None, controller=None):
        nonlocal calls
        calls += 1
        request_path = Path(arguments[arguments.index("--request") + 1])
        output_path = Path(arguments[arguments.index("--output") + 1])
        path = runner_module._load_json_report(request_path)["paths"][0]
        runner_module.save_json(
            output_path,
            {
                "mode": "check",
                "results": [
                    {
                        "path": path,
                        "status": "invalid",
                        "message": "temporary lock",
                    }
                ],
            },
        )

    monkeypatch.setattr(runner_module, "run_process", fake_run_process)
    cache = tmp_path / "cache"
    runner_module.check_odb_compatibility(
        "abaqus",
        [source],
        cache,
        abaqus_version="2025|abaqus",
    )
    second = runner_module.check_odb_compatibility(
        "abaqus",
        [source],
        cache,
        abaqus_version="2025|abaqus",
    )

    assert calls == 2
    assert second["cache_hits"] == 0


def test_upgrade_invalidates_old_initial_cache_and_caches_new_compatibility(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "model.odb"
    backup = tmp_path / "model-old.odb"
    old_content = bytearray(b"A" * (1024 * 1024))
    source.write_bytes(old_content)
    cache = tmp_path / "cache"
    old_fingerprint = quick_odb_fingerprint(source)
    save_json_cache(
        cache,
        "initial",
        source,
        old_fingerprint,
        "2025|abaqus",
        {"path": str(source), "steps": ["Old"]},
        config_hash=stable_config_hash(
            {
                "scanner": "scan_odb",
                "schema_version": CACHE_SCHEMA_VERSION,
            }
        ),
    )

    def fake_run_process(arguments, _cwd, log=None, controller=None):
        request_path = Path(arguments[arguments.index("--request") + 1])
        output_path = Path(arguments[arguments.index("--output") + 1])
        task = runner_module._load_json_report(request_path)["tasks"][0]
        # Simulate the worst case for a sampled fingerprint: the upgraded file
        # keeps the same size and changes only outside the head/middle/tail samples.
        new_content = bytearray(old_content)
        new_content[200_000] = ord("B")
        Path(task["source_path"]).write_bytes(new_content)
        runner_module.save_json(
            output_path,
            {
                "mode": "upgrade",
                "results": [
                    {
                        "source_path": task["source_path"],
                        "upgraded_path": task["upgraded_path"],
                        "backup_path": task["backup_path"],
                        "status": "upgraded",
                        "message": "ok",
                    }
                ],
            },
        )

    monkeypatch.setattr(runner_module, "run_process", fake_run_process)
    result = runner_module.upgrade_odb_files(
        "abaqus",
        [(source, backup)],
        cache,
        release="2025",
        abaqus_version="2025|abaqus",
    )

    new_fingerprint = quick_odb_fingerprint(source)
    assert new_fingerprint == old_fingerprint
    assert not cache_entry_dir(cache, "initial", old_fingerprint).exists()
    assert result["results"][0]["old_content_fingerprint"] == old_fingerprint
    assert result["results"][0]["content_fingerprint"] == new_fingerprint
    compatibility_snapshot = {
        "checker": "odb_compatibility",
        "schema_version": CACHE_SCHEMA_VERSION,
    }
    cached = load_json_cache(
        cache,
        "compatibility",
        new_fingerprint,
        "2025|abaqus",
        stable_config_hash(compatibility_snapshot),
    )
    assert cached is not None
    assert cached["status"] == "valid"


def test_parallel_upgrade_cancel_stops_pending_but_finishes_active(
    tmp_path: Path, monkeypatch
) -> None:
    sources = [tmp_path / f"model-{index}.odb" for index in range(5)]
    for index, source in enumerate(sources):
        source.write_bytes(f"old-{index}".encode("ascii"))
    controller = runner_module.UpgradeBatchController()
    started = 0
    started_lock = threading.Lock()
    two_started = threading.Event()
    release_active = threading.Event()

    def fake_run_process(arguments, _cwd, log=None, controller=None):
        nonlocal started
        request_path = Path(arguments[arguments.index("--request") + 1])
        output_path = Path(arguments[arguments.index("--output") + 1])
        task = runner_module._load_json_report(request_path)["tasks"][0]
        with started_lock:
            started += 1
            if started == 2:
                two_started.set()
        assert release_active.wait(timeout=5)
        source = Path(task["source_path"])
        source.write_bytes(source.read_bytes() + b"-upgraded")
        runner_module.save_json(
            output_path,
            {
                "mode": "upgrade",
                "results": [
                    {
                        "source_path": task["source_path"],
                        "upgraded_path": task["upgraded_path"],
                        "backup_path": task["backup_path"],
                        "status": "upgraded",
                        "message": "ok",
                    }
                ],
            },
        )

    monkeypatch.setattr(runner_module, "run_process", fake_run_process)
    holder: dict[str, dict] = {}

    def run_upgrade() -> None:
        holder["payload"] = runner_module.upgrade_odb_files(
            "abaqus",
            [
                (source, source.with_name(f"{source.stem}-old.odb"))
                for source in sources
            ],
            tmp_path / "cache",
            controller=controller,
            parallel_workers=2,
            abaqus_version="2025|abaqus",
        )

    thread = threading.Thread(target=run_upgrade)
    thread.start()
    assert two_started.wait(timeout=5)
    controller.cancel()
    release_active.set()
    thread.join(timeout=10)
    assert not thread.is_alive()

    payload = holder["payload"]
    assert started == 2
    assert payload["parallel_workers"] == 2
    assert payload["completed_count"] == 2
    assert payload["cancelled_count"] == 3
    assert [result["status"] for result in payload["results"]].count(
        "upgraded"
    ) == 2
    assert [result["status"] for result in payload["results"]].count(
        "cancelled"
    ) == 3


def test_numeric_cache_hash_excludes_render_only_settings() -> None:
    payload = {
        "start_step": "Load",
        "end_step": "Load",
        "selected_sequence_indices": [1, 2],
        "settings": {
            "axial_cut_count": 100,
            "pile_head_above_ground_mm": 500.0,
            "longitudinal_orientation_threshold": 0.95,
            "damage_threshold": 0.9,
            "image_width": 1500,
            "camera_view_vector": [1.0, 1.0, 0.5],
        },
    }
    first = stable_config_hash(numeric_config_snapshot(payload))
    payload["settings"]["image_width"] = 2400
    payload["settings"]["camera_view_vector"] = [0.0, 1.0, 0.0]
    assert stable_config_hash(numeric_config_snapshot(payload)) == first
    payload["settings"]["axial_cut_count"] = 80
    assert stable_config_hash(numeric_config_snapshot(payload)) != first
