from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from .cache import (
    CACHE_SCHEMA_VERSION,
    invalidate_content_fingerprint,
    load_json_cache,
    odb_file_metadata,
    quick_odb_fingerprint,
    save_json_cache,
    stable_config_hash,
)
from .config import abaqus_script, save_json
from .process_runner import *
from .process_runner import ProcessCancelled, ProcessController, run_process
from .runner_parallel import MultiProcessController


def _load_json_report(path: Path) -> dict:
    """Read reports produced by Abaqus across Chinese Windows encodings."""

    payload = path.read_bytes()
    decode_errors: list[UnicodeDecodeError] = []
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return json.loads(payload.decode(encoding))
        except UnicodeDecodeError as error:
            decode_errors.append(error)
    if decode_errors:
        raise decode_errors[0]
    raise ValueError(f"无法读取 Abaqus JSON 报告：{path}")


class UpgradeBatchController:
    """Stop scheduling upgrades without terminating an active ODB upgrade."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: set[Any] = set()
        self._stop_requested = False

    @property
    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested

    @property
    def cancel_requested(self) -> bool:
        # ``run_process`` must never treat a safely finishing upgrade as killed.
        return False

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(process.poll() is None for process in self._processes)

    def attach(self, process) -> None:
        with self._lock:
            self._processes.add(process)

    def detach(self, process) -> None:
        with self._lock:
            self._processes.discard(process)

    def cancel(self) -> None:
        with self._lock:
            self._stop_requested = True


def scan_folder(
    abaqus_command: str,
    odb_folder: Path,
    cache_dir: Path,
    log: LogCallback | None = None,
    controller: ProcessController | MultiProcessController | None = None,
    selected_paths: Iterable[Path | str] | None = None,
    parallel_workers: int = 1,
    force_rescan: bool = False,
    abaqus_version: str = "",
) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = cache_dir / "scan_report.json"
    selected = (
        None
        if selected_paths is None
        else [Path(value).resolve() for value in selected_paths]
    )
    workers = max(1, min(int(parallel_workers), 4))
    if selected is not None:
        version = str(abaqus_version or abaqus_command)
        scan_snapshot = {
            "scanner": "scan_odb",
            "schema_version": CACHE_SCHEMA_VERSION,
        }
        config_hash = stable_config_hash(scan_snapshot)
        fingerprints: dict[str, str] = {}
        results: dict[str, dict] = {}
        misses: list[Path] = []
        for path in selected:
            fingerprint = quick_odb_fingerprint(path)
            fingerprints[str(path)] = fingerprint
            cached = None if force_rescan else load_json_cache(
                cache_dir,
                "initial",
                fingerprint,
                version,
                config_hash,
            )
            if cached is None:
                misses.append(path)
                if log:
                    log(f"CACHE_MISS|initial|{path.name}")
                continue
            item = dict(cached)
            item.update(
                {
                    "path": str(path),
                    "content_fingerprint": fingerprint,
                    **odb_file_metadata(path),
                }
            )
            results[str(path)] = item
            if log:
                log(f"CACHE_HIT|initial|{path.name}|{fingerprint[:12]}")

        if log:
            log(f"SCAN_DISCOVERED|{len(selected)}")
            for completed, path in enumerate(
                (path for path in selected if str(path) in results), 1
            ):
                log(f"SCAN_DONE|{completed}|{len(selected)}|{path.name}")

        scanned_payload = {"odbs": [], "cancelled": False}
        if misses:
            cached_count = len(results)
            scanned_payload = _scan_selected_paths_parallel(
                abaqus_command,
                odb_folder.resolve(),
                cache_dir,
                output,
                misses,
                min(workers, len(misses)),
                log,
                controller,
                progress_start=cached_count,
                progress_total=len(selected),
                emit_discovered=False,
            )
            for item in scanned_payload.get("odbs", []):
                path = Path(item["path"]).resolve()
                fingerprint = fingerprints[str(path)]
                enriched = dict(item)
                enriched.update(
                    {
                        "path": str(path),
                        "content_fingerprint": fingerprint,
                        **odb_file_metadata(path),
                    }
                )
                results[str(path)] = enriched
                if not enriched.get("error"):
                    save_json_cache(
                        cache_dir,
                        "initial",
                        path,
                        fingerprint,
                        version,
                        enriched,
                        config_hash=config_hash,
                        config_snapshot=scan_snapshot,
                    )

        ordered = [results[str(path)] for path in selected if str(path) in results]
        payload = {
            "folder": str(odb_folder.resolve()),
            "discovered_count": len(selected),
            "odb_count": len(selected),
            "completed_count": len(ordered),
            "cancelled": bool(scanned_payload.get("cancelled", False)),
            "selected_paths": [str(path) for path in selected],
            "odbs": ordered,
            "parallel_workers": min(workers, max(len(misses), 1)),
            "cache_root": str(cache_dir),
            "cache_hits": len(selected) - len(misses),
        }
        save_json(output, payload)
        if log:
            log(f"SCAN_FINISHED|{len(ordered)}|{len(selected)}")
        return payload
    arguments = [
        abaqus_command,
        "python",
        str(abaqus_script("scan_odb.py")),
        "--folder",
        str(odb_folder),
        "--output",
        str(output),
    ]
    if selected is not None:
        selection_path = cache_dir / "scan_selection.json"
        paths = [str(value) for value in selected]
        selection_path.write_text(
            json.dumps({"folder": str(odb_folder.resolve()), "paths": paths}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        arguments.extend(["--selection", str(selection_path)])
    try:
        run_process(arguments, cache_dir, log, controller)
    except ProcessCancelled:
        if not output.exists():
            raise
        with output.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        payload["cancelled"] = True
        return payload
    with output.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _scan_selected_paths_parallel(
    abaqus_command: str,
    odb_folder: Path,
    cache_dir: Path,
    output: Path,
    selected_paths: list[Path],
    workers: int,
    log: LogCallback | None,
    controller: ProcessController | MultiProcessController | None,
    *,
    progress_start: int = 0,
    progress_total: int | None = None,
    emit_discovered: bool = True,
) -> dict:
    """Scan selected ODBs in independent Abaqus Python processes."""

    total = len(selected_paths)
    displayed_total = progress_total if progress_total is not None else total
    results: dict[str, dict] = {}
    completed = 0
    completion_lock = threading.Lock()

    def report(cancelled: bool = False) -> dict:
        ordered = [
            results[str(path)]
            for path in selected_paths
            if str(path) in results
        ]
        payload = {
            "folder": str(odb_folder),
            "discovered_count": total,
            "odb_count": total,
            "completed_count": len(ordered),
            "cancelled": cancelled,
            "selected_paths": [str(path) for path in selected_paths],
            "odbs": ordered,
            "parallel_workers": workers,
        }
        save_json(output, payload)
        return payload

    def scan_one(index: int, path: Path) -> tuple[str, dict]:
        if controller is not None and controller.cancel_requested:
            raise ProcessCancelled("Process cancelled by user")
        digest = hashlib.sha1(str(path).casefold().encode("utf-8")).hexdigest()[:10]
        job_dir = cache_dir / "initial_scan" / f"{index:04d}_{digest}"
        job_dir.mkdir(parents=True, exist_ok=True)
        selection_path = job_dir / "scan_selection.json"
        result_path = job_dir / "scan_report.json"
        save_json(
            selection_path,
            {"folder": str(odb_folder), "paths": [str(path)]},
        )
        result_path.unlink(missing_ok=True)
        if log:
            log(
                f"SCAN_START|{progress_start + index}|"
                f"{displayed_total}|{path.name}"
            )

        def child_log(message: str) -> None:
            if log and not message.startswith("SCAN_") and not message.startswith('{"output"'):
                log(f"[{path.name}] {message}")

        run_process(
            [
                abaqus_command,
                "python",
                str(abaqus_script("scan_odb.py")),
                "--folder",
                str(odb_folder),
                "--output",
                str(result_path),
                "--selection",
                str(selection_path),
            ],
            job_dir,
            child_log,
            controller,
        )
        payload = _load_json_report(result_path)
        odbs = payload.get("odbs", [])
        if len(odbs) != 1:
            raise RuntimeError(f"ODB 扫描未返回唯一结果：{path}")
        return str(path), odbs[0]

    if log and emit_discovered:
        log(f"SCAN_DISCOVERED|{displayed_total}")
    try:
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="odb-initial-scan"
        ) as executor:
            futures = {
                executor.submit(scan_one, index, path): path
                for index, path in enumerate(selected_paths, 1)
            }
            for future in as_completed(futures):
                path = futures[future]
                try:
                    result_path, item = future.result()
                except ProcessCancelled:
                    continue
                results[result_path] = item
                with completion_lock:
                    completed += 1
                    done = completed
                report(False)
                if log:
                    log(
                        f"SCAN_DONE|{progress_start + done}|"
                        f"{displayed_total}|{path.name}"
                    )
    except Exception:
        if controller is not None:
            controller.cancel()
        raise

    cancelled = bool(controller is not None and controller.cancel_requested)
    payload = report(cancelled)
    if log and emit_discovered:
        log(
            f"SCAN_FINISHED|{progress_start + payload['completed_count']}|"
            f"{displayed_total}"
        )
    return payload


def _unique_sibling_path(source_path: Path, suffix: str) -> Path:
    source = source_path.resolve()
    base = source.with_name(f"{source.stem}{suffix}.odb")
    if not base.exists():
        return base
    for index in range(2, 10000):
        candidate = source.with_name(f"{source.stem}{suffix}-{index}.odb")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成唯一 ODB 文件名：{source}")


def upgrade_backup_path(source_path: Path) -> Path:
    """Return a non-existing ``-old`` backup path next to the source ODB."""

    return _unique_sibling_path(source_path, "-old")


def upgrade_temporary_path(source_path: Path, release: str = "2025") -> Path:
    """Return a temporary ODB path used before the validated file swap."""

    return _unique_sibling_path(source_path, f"-upgrading-{release}")


def upgrade_target_path(source_path: Path, release: str = "2025") -> Path:
    """Backward-compatible name for the old-ODB backup destination."""

    del release
    return upgrade_backup_path(source_path)


def check_odb_compatibility(
    abaqus_command: str,
    paths: Iterable[Path | str],
    cache_dir: Path,
    log: LogCallback | None = None,
    controller: ProcessController | None = None,
    *,
    force_rescan: bool = False,
    abaqus_version: str = "",
) -> dict:
    """Check whether ODB files are readable by the configured Abaqus release."""

    cache_dir.mkdir(parents=True, exist_ok=True)
    selected = [Path(value).resolve() for value in paths]
    version = str(abaqus_version or abaqus_command)
    snapshot = {
        "checker": "odb_compatibility",
        "schema_version": CACHE_SCHEMA_VERSION,
    }
    config_hash = stable_config_hash(snapshot)
    fingerprints: dict[str, str] = {}
    results: dict[str, dict] = {}
    misses: list[Path] = []

    for path in selected:
        path_key = str(path)
        if not path.is_file() or path.stat().st_size <= 0:
            misses.append(path)
            if log:
                log(f"COMPAT_CACHE_MISS|{path.name}")
            continue
        fingerprint = quick_odb_fingerprint(path)
        fingerprints[path_key] = fingerprint
        cached = None if force_rescan else load_json_cache(
            cache_dir,
            "compatibility",
            fingerprint,
            version,
            config_hash,
        )
        if cached is None:
            misses.append(path)
            if log:
                log(f"COMPAT_CACHE_MISS|{path.name}|{fingerprint[:12]}")
            continue
        item = dict(cached)
        item.update(
            {
                "path": path_key,
                "size_bytes": path.stat().st_size,
                "content_fingerprint": fingerprint,
                **odb_file_metadata(path),
            }
        )
        results[path_key] = item
        if log:
            log(f"COMPAT_CACHE_HIT|{path.name}|{fingerprint[:12]}")

    request_path = cache_dir / "odb_compatibility_check_request.json"
    output_path = cache_dir / "odb_compatibility_check_report.json"
    if misses:
        request_path.write_text(
            json.dumps(
                {
                    "paths": [str(path) for path in misses],
                    "progress_start": len(results),
                    "progress_total": len(selected),
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        output_path.unlink(missing_ok=True)
        run_process(
            [
                abaqus_command,
                "python",
                str(abaqus_script("odb_compatibility.py")),
                "--mode",
                "check",
                "--request",
                str(request_path),
                "--output",
                str(output_path),
            ],
            cache_dir,
            log,
            controller,
        )
        checked = _load_json_report(output_path)
        for raw_item in checked.get("results", []):
            item = dict(raw_item)
            path = Path(item["path"]).resolve()
            path_key = str(path)
            fingerprint = fingerprints.get(path_key)
            if fingerprint is not None and path.is_file():
                current_fingerprint = quick_odb_fingerprint(path)
                if current_fingerprint != fingerprint:
                    item.update(
                        status="invalid",
                        message="ODB 文件在兼容性检测期间发生变化，请重新检测",
                    )
                else:
                    item.update(
                        {
                            "path": path_key,
                            "content_fingerprint": fingerprint,
                            **odb_file_metadata(path),
                        }
                    )
                    if item.get("status") in {
                        "valid",
                        "upgrade_required",
                        "newer_release",
                    }:
                        save_json_cache(
                            cache_dir,
                            "compatibility",
                            path,
                            fingerprint,
                            version,
                            item,
                            config_hash=config_hash,
                            config_snapshot=snapshot,
                        )
            results[path_key] = item

    ordered = [results[str(path)] for path in selected if str(path) in results]
    payload = {
        "mode": "check",
        "results": ordered,
        "cache_hits": len(selected) - len(misses),
        "cache_misses": len(misses),
    }
    save_json(output_path, payload)
    return payload


def upgrade_odb_files(
    abaqus_command: str,
    tasks: Iterable[tuple[Path | str, Path | str]],
    cache_dir: Path,
    log: LogCallback | None = None,
    controller: UpgradeBatchController | None = None,
    release: str = "2025",
    abaqus_version: str = "",
    parallel_workers: int = 1,
) -> dict:
    """Upgrade independent ODBs concurrently while retaining ``name-old.odb``."""

    cache_dir.mkdir(parents=True, exist_ok=True)
    serialized_tasks = [
        {
            "source_path": str(Path(source).resolve()),
            "upgraded_path": str(Path(source).resolve()),
            "backup_path": str(Path(backup).resolve()),
            "temporary_path": str(
                upgrade_temporary_path(Path(source), release).resolve()
            ),
        }
        for source, backup in tasks
    ]
    if not serialized_tasks:
        return {
            "mode": "upgrade",
            "results": [],
            "completed_count": 0,
            "cancelled_count": 0,
        }

    workers = max(1, min(int(parallel_workers), 4, len(serialized_tasks)))
    work_root = cache_dir / "upgrade_work"
    work_root.mkdir(parents=True, exist_ok=True)
    output_path = cache_dir / "odb_compatibility_upgrade_report.json"
    output_path.unlink(missing_ok=True)
    version = str(abaqus_version or f"{release}|{abaqus_command}")
    snapshot = {
        "checker": "odb_compatibility",
        "schema_version": CACHE_SCHEMA_VERSION,
    }
    config_hash = stable_config_hash(snapshot)
    cache_lock = threading.Lock()
    start_lock = threading.Lock()
    started_count = 0

    def cancelled_result(task: dict[str, str]) -> dict[str, Any]:
        return {
            "source_path": task["source_path"],
            "upgraded_path": task["upgraded_path"],
            "backup_path": task["backup_path"],
            "status": "cancelled",
            "message": "本批升级已取消，尚未处理",
        }

    def process_one(
        task_index: int, task: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        nonlocal started_count
        if controller is not None and controller.stop_requested:
            return task_index, cancelled_result(task)

        with start_lock:
            started_count += 1
            start_number = started_count
        source = Path(task["source_path"])
        if log:
            log(
                f"ODB_UPGRADE_START|{start_number}|{len(serialized_tasks)}|"
                f"{source.name}"
            )
        base_result: dict[str, Any] = {
            "source_path": task["source_path"],
            "upgraded_path": task["upgraded_path"],
            "backup_path": task["backup_path"],
        }
        if not source.is_file():
            return task_index, {
                **base_result,
                "status": "missing",
                "message": "源文件不存在",
            }
        if Path(task["backup_path"]).exists():
            return task_index, {
                **base_result,
                "status": "target_exists",
                "message": "旧版 ODB 备份已存在，拒绝覆盖",
            }
        if Path(task["temporary_path"]).exists():
            return task_index, {
                **base_result,
                "status": "target_exists",
                "message": "升级临时文件已存在，拒绝覆盖",
            }

        try:
            old_fingerprint = quick_odb_fingerprint(source)
            task_key = hashlib.sha256(
                task["source_path"].encode("utf-8")
            ).hexdigest()[:12]
            task_dir = work_root / f"{task_index + 1:04d}_{task_key}"
            task_dir.mkdir(parents=True, exist_ok=True)
            request_path = task_dir / "request.json"
            task_output_path = task_dir / "report.json"
            request_path.write_text(
                json.dumps({"tasks": [task]}, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            task_output_path.unlink(missing_ok=True)

            def child_log(message: str) -> None:
                if log and not message.startswith("ODB_UPGRADE_"):
                    log(f"[{source.name}] {message}")

            run_process(
                [
                    abaqus_command,
                    "python",
                    str(abaqus_script("odb_compatibility.py")),
                    "--mode",
                    "upgrade",
                    "--request",
                    str(request_path),
                    "--output",
                    str(task_output_path),
                ],
                task_dir,
                child_log,
                controller,
            )
            task_payload = _load_json_report(task_output_path)
            raw_results = task_payload.get("results", [])
            if len(raw_results) != 1:
                raise RuntimeError(f"ODB 升级未返回唯一结果：{source}")
            result = dict(raw_results[0])
        except Exception as error:
            return task_index, {
                **base_result,
                "status": "upgrade_failed",
                "message": str(error),
            }

        if result.get("status") != "upgraded":
            return task_index, result
        try:
            upgraded_path = Path(result["upgraded_path"]).resolve()
            new_fingerprint = quick_odb_fingerprint(upgraded_path)
            with cache_lock:
                removed = invalidate_content_fingerprint(
                    cache_dir, old_fingerprint
                )
                compatibility_result = {
                    "path": str(upgraded_path),
                    "status": "valid",
                    "message": f"已升级到 Abaqus {release}，并通过读取验证",
                    "size_bytes": upgraded_path.stat().st_size,
                    "content_fingerprint": new_fingerprint,
                    **odb_file_metadata(upgraded_path),
                }
                save_json_cache(
                    cache_dir,
                    "compatibility",
                    upgraded_path,
                    new_fingerprint,
                    version,
                    compatibility_result,
                    config_hash=config_hash,
                    config_snapshot=snapshot,
                )
            if log:
                log(
                    f"CACHE_INVALIDATED|upgrade|{source.name}|"
                    f"{old_fingerprint[:12]}|{len(removed)}"
                )
            result["old_content_fingerprint"] = old_fingerprint
            result["content_fingerprint"] = new_fingerprint
        except Exception as error:
            result["cache_warning"] = str(error)
            result["message"] = (
                f"{result.get('message', '升级完成')}；"
                "缓存失效失败，请对该 ODB 执行一次强制重新扫描"
            )
            if log:
                log(f"CACHE_INVALIDATE_FAILED|{source.name}|{error}")
        return task_index, result

    results_by_index: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="odb-upgrade"
    ) as pool:
        futures = {
            pool.submit(process_one, index, task): (index, task)
            for index, task in enumerate(serialized_tasks)
        }
        for completed, future in enumerate(as_completed(futures), 1):
            index, task = futures[future]
            try:
                result_index, result = future.result()
            except Exception as error:
                result_index = index
                result = {
                    "source_path": task["source_path"],
                    "upgraded_path": task["upgraded_path"],
                    "backup_path": task["backup_path"],
                    "status": "upgrade_failed",
                    "message": str(error),
                }
            results_by_index[result_index] = result
            if log:
                log(
                    f"ODB_UPGRADE_DONE|{completed}|{len(serialized_tasks)}|"
                    f"{result.get('status', 'upgrade_failed')}|"
                    f"{Path(task['source_path']).name}"
                )

    ordered = [
        results_by_index.get(index, cancelled_result(task))
        for index, task in enumerate(serialized_tasks)
    ]
    payload = {
        "mode": "upgrade",
        "results": ordered,
        "parallel_workers": workers,
        "completed_count": sum(
            result.get("status") != "cancelled" for result in ordered
        ),
        "cancelled_count": sum(
            result.get("status") == "cancelled" for result in ordered
        ),
    }
    save_json(output_path, payload)
    return payload
