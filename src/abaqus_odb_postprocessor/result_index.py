"""Persistent, time-directory-scoped indexes for generated result files."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .file_attributes import ensure_windows_hidden


INDEX_FILENAME = "_AbaqusODBPostProcessor_ResultIndex.sqlite3"
INDEX_LOCK_FILENAME = "_AbaqusODBPostProcessor_ResultIndex.lock"
INDEX_SCHEMA_VERSION = 1
_TEMP_PREFIX = f"{INDEX_FILENAME}.tmp-"
_EXCLUDED_SUFFIXES = {".part", ".download", ".crdownload"}


class ResultIndexError(RuntimeError):
    """Base exception for result-index operations."""


class ResultIndexCancelled(ResultIndexError):
    """Raised after a cooperative index cancellation request."""


class ResultIndexLocked(ResultIndexError):
    """Raised when another live process owns the time-directory index lock."""


class ResultIndexInvalid(ResultIndexError):
    """Raised when an index is corrupt, stale, or belongs to another directory."""


@dataclass(frozen=True)
class LoadedResultIndex:
    records: list[dict[str, Any]]
    metadata: dict[str, str]


def index_path(time_directory: Path | str) -> Path:
    return Path(time_directory).resolve() / INDEX_FILENAME


def lock_path(time_directory: Path | str) -> Path:
    return Path(time_directory).resolve() / INDEX_LOCK_FILENAME


def _canonical(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _child_directories(time_directory: Path) -> list[str]:
    names: list[str] = []
    try:
        entries = list(os.scandir(time_directory))
    except OSError as error:
        raise ResultIndexInvalid(f"无法读取时间目录：{error}") from error
    for entry in entries:
        path = Path(entry.path)
        try:
            if entry.is_dir(follow_symlinks=False) and not _is_link_or_junction(path):
                names.append(entry.name)
        except OSError:
            continue
    return sorted(names, key=str.casefold)


def directory_identity(time_directory: Path | str) -> dict[str, Any]:
    directory = Path(time_directory).resolve()
    snapshot = {
        "canonical_path": _canonical(directory),
        "directory_name": directory.name,
        "child_directories": _child_directories(directory),
    }
    encoded = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    snapshot["identity_hash"] = hashlib.sha256(encoded).hexdigest()
    return snapshot


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
    else:
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS records (
            relative_path TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            suffix TEXT NOT NULL,
            use_case TEXT NOT NULL,
            description TEXT NOT NULL,
            recommended INTEGER NOT NULL,
            section TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS records_section
            ON records(section, recommended DESC, use_case, name);
        CREATE INDEX IF NOT EXISTS records_use_case
            ON records(use_case, name);
        """
    )


def _write_metadata(
    connection: sqlite3.Connection,
    time_directory: Path,
    *,
    operation: str,
) -> dict[str, str]:
    identity = directory_identity(time_directory)
    values = {
        "schema_version": str(INDEX_SCHEMA_VERSION),
        "canonical_path": str(identity["canonical_path"]),
        "directory_name": str(identity["directory_name"]),
        "child_directories": json.dumps(
            identity["child_directories"], ensure_ascii=False, separators=(",", ":")
        ),
        "identity_hash": str(identity["identity_hash"]),
        "updated_at": _utc_now(),
        "operation": operation,
        "record_count": str(
            connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        ),
    }
    connection.executemany(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
        values.items(),
    )
    return values


def _read_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    try:
        return {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key, value FROM metadata")
        }
    except sqlite3.Error as error:
        raise ResultIndexInvalid(f"索引元数据不可读：{error}") from error


def _validate_connection(
    connection: sqlite3.Connection, time_directory: Path
) -> dict[str, str]:
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as error:
        raise ResultIndexInvalid(f"SQLite 完整性检查失败：{error}") from error
    if not quick_check or str(quick_check[0]).casefold() != "ok":
        raise ResultIndexInvalid("SQLite 完整性检查未通过")
    metadata = _read_metadata(connection)
    if metadata.get("schema_version") != str(INDEX_SCHEMA_VERSION):
        raise ResultIndexInvalid(
            f"索引版本过旧或不兼容：{metadata.get('schema_version', '未知')}"
        )
    identity = directory_identity(time_directory)
    expected = {
        "canonical_path": str(identity["canonical_path"]),
        "directory_name": str(identity["directory_name"]),
        "identity_hash": str(identity["identity_hash"]),
    }
    mismatches = [
        key for key, value in expected.items() if metadata.get(key) != value
    ]
    if mismatches:
        raise ResultIndexInvalid(
            "索引与当前时间目录不匹配：" + "、".join(mismatches)
        )
    return metadata


def load_result_index(time_directory: Path | str) -> LoadedResultIndex:
    directory = Path(time_directory).resolve()
    database = index_path(directory)
    if not database.is_file():
        raise ResultIndexInvalid("索引文件不存在")
    try:
        with closing(_connect(database, read_only=True)) as connection:
            metadata = _validate_connection(connection, directory)
            rows = connection.execute(
                """
                SELECT relative_path, name, suffix, use_case, description,
                       recommended, section, size, mtime_ns, error
                FROM records
                ORDER BY recommended DESC, use_case COLLATE NOCASE,
                         name COLLATE NOCASE, relative_path COLLATE NOCASE
                """
            ).fetchall()
    except sqlite3.Error as error:
        raise ResultIndexInvalid(f"无法打开索引：{error}") from error
    records = [dict(row) for row in rows]
    return LoadedResultIndex(records=records, metadata=metadata)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class _DirectoryLock:
    def __init__(self, time_directory: Path) -> None:
        self.path = lock_path(time_directory)
        self.token = uuid.uuid4().hex
        self._owned = False

    def __enter__(self) -> "_DirectoryLock":
        payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": _utc_now(),
            "token": self.token,
        }
        for attempt in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                if attempt or not self._remove_stale_lock():
                    raise ResultIndexLocked("另一程序实例正在索引该时间目录")
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            self._owned = True
            return self
        raise ResultIndexLocked("无法获得索引写入锁")

    def _remove_stale_lock(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if str(payload.get("host", "")) != socket.gethostname():
            return False
        try:
            pid = int(payload.get("pid", -1))
        except (TypeError, ValueError):
            return False
        if _pid_is_running(pid):
            return False
        try:
            self.path.unlink()
        except OSError:
            return False
        return True

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        if not self._owned:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def _excluded(path: Path) -> bool:
    name = path.name
    casefolded = name.casefold()
    if casefolded in {
        INDEX_FILENAME.casefold(),
        INDEX_LOCK_FILENAME.casefold(),
        f"{INDEX_FILENAME}-wal".casefold(),
        f"{INDEX_FILENAME}-shm".casefold(),
    }:
        return True
    if casefolded.startswith(_TEMP_PREFIX.casefold()):
        return True
    if path.suffix.casefold() in _EXCLUDED_SUFFIXES:
        return True
    return casefolded.endswith(".tmp") or ".tmp-" in casefolded


def _iter_files(
    roots: Iterable[Path],
    time_directory: Path,
    cancel_event: threading.Event,
) -> Iterable[tuple[Path, os.stat_result | None, str]]:
    stack = [path.resolve() for path in roots]
    while stack:
        if cancel_event.is_set():
            raise ResultIndexCancelled("索引任务已取消")
        current = stack.pop()
        if _is_link_or_junction(current):
            continue
        try:
            entries = list(os.scandir(current))
        except OSError as error:
            yield current, None, str(error)
            continue
        entries.sort(key=lambda entry: entry.name.casefold(), reverse=True)
        for entry in entries:
            if cancel_event.is_set():
                raise ResultIndexCancelled("索引任务已取消")
            path = Path(entry.path)
            if _excluded(path):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not _is_link_or_junction(path):
                        stack.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                yield path, None, str(error)
                continue
            try:
                path.relative_to(time_directory)
            except ValueError:
                continue
            yield path, stat, ""


def _record_row(
    path: Path,
    stat: os.stat_result | None,
    error: str,
    time_directory: Path,
    classify: Callable[[Path], Any],
    section_for: Callable[[Any], str],
) -> tuple[Any, ...]:
    relative = str(path.relative_to(time_directory))
    record = classify(path)
    return (
        relative,
        path.name,
        path.suffix.casefold(),
        str(record.use_case),
        str(record.description),
        int(bool(record.recommended)),
        str(section_for(record)),
        int(stat.st_size if stat is not None else -1),
        int(
            getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
            if stat is not None
            else 0
        ),
        error,
    )


def _insert_rows(
    connection: sqlite3.Connection,
    roots: Iterable[Path],
    time_directory: Path,
    classify: Callable[[Path], Any],
    section_for: Callable[[Any], str],
    cancel_event: threading.Event,
    progress: Callable[[int, str], None] | None,
) -> int:
    count = 0
    batch: list[tuple[Any, ...]] = []
    statement = """
        INSERT OR REPLACE INTO records(
            relative_path, name, suffix, use_case, description, recommended,
            section, size, mtime_ns, error
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    for path, stat, error in _iter_files(roots, time_directory, cancel_event):
        if path == time_directory and stat is None:
            continue
        try:
            row = _record_row(
                path, stat, error, time_directory, classify, section_for
            )
        except (OSError, ValueError) as record_error:
            row = (
                str(path.relative_to(time_directory)),
                path.name,
                path.suffix.casefold(),
                "无法读取的文件",
                "建立索引时无法读取该文件的元数据",
                0,
                "other",
                -1,
                0,
                str(record_error),
            )
        batch.append(row)
        count += 1
        if len(batch) >= 500:
            connection.executemany(statement, batch)
            batch.clear()
        if progress is not None and (count == 1 or count % 250 == 0):
            progress(count, row[0])
    if batch:
        connection.executemany(statement, batch)
    return count


def _temporary_database(time_directory: Path) -> Path:
    return time_directory / (
        f"{_TEMP_PREFIX}{os.getpid()}-{uuid.uuid4().hex}.sqlite3"
    )


def _replace_database(temporary: Path, target: Path) -> None:
    with closing(_connect(temporary, read_only=True)) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or str(result[0]).casefold() != "ok":
            raise ResultIndexInvalid("新索引未通过 SQLite 完整性检查")
    for attempt in range(2):
        try:
            os.replace(temporary, target)
            ensure_windows_hidden(target)
            return
        except PermissionError:
            if attempt:
                raise
            time.sleep(0.25)


def build_result_index(
    time_directory: Path | str,
    classify: Callable[[Path], Any],
    section_for: Callable[[Any], str],
    cancel_event: threading.Event | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, str]:
    directory = Path(time_directory).resolve()
    cancellation = cancel_event or threading.Event()
    temporary = _temporary_database(directory)
    target = index_path(directory)
    with _DirectoryLock(directory):
        remove_orphaned_temporary_indexes(directory)
        try:
            with closing(_connect(temporary)) as connection:
                _create_schema(connection)
                _insert_rows(
                    connection,
                    [directory],
                    directory,
                    classify,
                    section_for,
                    cancellation,
                    progress,
                )
                metadata = _write_metadata(
                    connection, directory, operation="full"
                )
                connection.commit()
            if cancellation.is_set():
                raise ResultIndexCancelled("索引任务已取消")
            _replace_database(temporary, target)
            return metadata
        finally:
            temporary.unlink(missing_ok=True)


def update_result_index_scopes(
    time_directory: Path | str,
    scopes: Iterable[Path | str],
    classify: Callable[[Path], Any],
    section_for: Callable[[Any], str],
    cancel_event: threading.Event | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, str]:
    directory = Path(time_directory).resolve()
    resolved_scopes = [Path(scope).resolve() for scope in scopes]
    for scope in resolved_scopes:
        try:
            scope.relative_to(directory)
        except ValueError as error:
            raise ResultIndexInvalid(
                f"增量索引目录不属于当前时间目录：{scope}"
            ) from error
    cancellation = cancel_event or threading.Event()
    temporary = _temporary_database(directory)
    target = index_path(directory)
    with _DirectoryLock(directory):
        remove_orphaned_temporary_indexes(directory)
        try:
            if target.is_file():
                with closing(
                    _connect(target, read_only=True)
                ) as source, closing(_connect(temporary)) as destination:
                    source.backup(destination)
            with closing(_connect(temporary)) as connection:
                _create_schema(connection)
                for scope in resolved_scopes:
                    relative = str(scope.relative_to(directory))
                    descendant_prefix = f"{relative}{os.sep}"
                    connection.execute(
                        "DELETE FROM records WHERE relative_path = ? "
                        "OR substr(relative_path, 1, ?) = ?",
                        (
                            relative,
                            len(descendant_prefix),
                            descendant_prefix,
                        ),
                    )
                _insert_rows(
                    connection,
                    [scope for scope in resolved_scopes if scope.is_dir()],
                    directory,
                    classify,
                    section_for,
                    cancellation,
                    progress,
                )
                metadata = _write_metadata(
                    connection, directory, operation="incremental"
                )
                connection.commit()
            if cancellation.is_set():
                raise ResultIndexCancelled("索引任务已取消")
            _replace_database(temporary, target)
            return metadata
        finally:
            temporary.unlink(missing_ok=True)


def remove_orphaned_temporary_indexes(time_directory: Path | str) -> int:
    """Remove only this process-independent index temp files after lock recovery."""

    directory = Path(time_directory).resolve()
    removed = 0
    for path in directory.glob(f"{_TEMP_PREFIX}*.sqlite3"):
        try:
            if time.time() - path.stat().st_mtime < 24 * 3600:
                continue
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed
