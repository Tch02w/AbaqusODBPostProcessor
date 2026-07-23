from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

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


def scan_folder(
    abaqus_command: str,
    odb_folder: Path,
    cache_dir: Path,
    log: LogCallback | None = None,
    controller: ProcessController | MultiProcessController | None = None,
    selected_paths: Iterable[Path | str] | None = None,
    parallel_workers: int = 1,
) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = cache_dir / "scan_report.json"
    selected = (
        None
        if selected_paths is None
        else [Path(value).resolve() for value in selected_paths]
    )
    workers = max(1, min(int(parallel_workers), 4))
    if selected is not None and len(selected) > 1 and workers > 1:
        return _scan_selected_paths_parallel(
            abaqus_command,
            odb_folder.resolve(),
            cache_dir,
            output,
            selected,
            min(workers, len(selected)),
            log,
            controller,
        )
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
) -> dict:
    """Scan selected ODBs in independent Abaqus Python processes."""

    total = len(selected_paths)
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
            log(f"SCAN_START|{index}|{total}|{path.name}")

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

    if log:
        log(f"SCAN_DISCOVERED|{total}")
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
                    log(f"SCAN_DONE|{done}|{total}|{path.name}")
    except Exception:
        if controller is not None:
            controller.cancel()
        raise

    cancelled = bool(controller is not None and controller.cancel_requested)
    payload = report(cancelled)
    if log:
        log(f"SCAN_FINISHED|{payload['completed_count']}|{total}")
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
) -> dict:
    """Check whether ODB files are readable by the configured Abaqus release."""

    cache_dir.mkdir(parents=True, exist_ok=True)
    request_path = cache_dir / "odb_compatibility_check_request.json"
    output_path = cache_dir / "odb_compatibility_check_report.json"
    request_path.write_text(
        json.dumps(
            {"paths": [str(Path(value).resolve()) for value in paths]},
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
    return _load_json_report(output_path)


def upgrade_odb_files(
    abaqus_command: str,
    tasks: Iterable[tuple[Path | str, Path | str]],
    cache_dir: Path,
    log: LogCallback | None = None,
    controller: ProcessController | None = None,
    release: str = "2025",
) -> dict:
    """Upgrade ODBs in place after validation, retaining ``name-old.odb``."""

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
        return {"mode": "upgrade", "results": []}
    for task in serialized_tasks:
        if not Path(task["source_path"]).is_file():
            raise FileNotFoundError(f"升级源文件不存在：{task['source_path']}")
        if Path(task["backup_path"]).exists():
            raise FileExistsError(
                f"旧版 ODB 备份已存在，拒绝覆盖：{task['backup_path']}"
            )
        if Path(task["temporary_path"]).exists():
            raise FileExistsError(
                f"升级临时文件已存在，拒绝覆盖：{task['temporary_path']}"
            )
    request_path = cache_dir / "odb_compatibility_upgrade_request.json"
    output_path = cache_dir / "odb_compatibility_upgrade_report.json"
    request_path.write_text(
        json.dumps({"tasks": serialized_tasks}, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    output_path.unlink(missing_ok=True)
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
            str(output_path),
        ],
        cache_dir,
        log,
        controller,
    )
    return _load_json_report(output_path)
