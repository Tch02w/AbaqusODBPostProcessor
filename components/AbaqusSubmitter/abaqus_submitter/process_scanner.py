import json
import os
import re
import subprocess
import time

try:
    import psutil
except ImportError:
    psutil = None

from .abaqus_diagnostics import inspect_job_files
from .constants import (
    ABAQUS_MEMORY_POLL_INTERVAL_SECONDS,
    ABAQUS_PROCESS_EXACT_NAMES,
    ABAQUS_PROCESS_NAME_MARKERS,
    ABAQUS_PROCESS_SUBSTRINGS,
    COMMAND_PARAMETER_PATTERN_CACHE,
    JOB_NAME_COMMAND_PATTERNS,
    PROCESS_SNAPSHOT_CACHE_SECONDS,
    STALE_LOCK_GRACE_SECONDS,
    STATUS_COMPLETED,
    STATUS_CONFIRMING,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_TERMINATED,
    STATUS_UNKNOWN,
)
from .diagnostics import hang_probe_log, performance_log as log_performance
from .queue_scheduler import normalize_work_dir


abaqus_memory_cache = {"timestamp": 0.0, "usage": {}}
process_snapshot_cache = {
    False: {"timestamp": 0.0, "rows": []},
    True: {"timestamp": 0.0, "rows": []},
}

DETAIL_PROCESS_EXACT_NAMES = {
    "abaqus",
    "abq",
    "standard",
    "explicit",
    "package",
    "packager",
    "pre",
    "smalauncher",
    "smapython",
    "smasimutility",
    "eqsequationsolver",
}
DETAIL_PROCESS_PREFIXES = (
    "standard",
    "explicit",
    "sma",
)
GENERIC_PROCESS_NAMES_REQUIRING_CMDLINE = {
    "cmd",
    "python",
    "pythonw",
    "powershell",
    "pwsh",
    "smapython",
}
ABAQUS_COMMAND_LINE_MARKERS = (
    "abaqus",
    "job=",
    "oldjob=",
    "input=",
    "run standard",
    "run explicit",
    "standard.exe",
    "explicit.exe",
    "datacheck",
    "interactive",
    "simulia",
)
EXTERNAL_SCAN_MAX_CHILD_DEPTH = 2


def normalize_joblist_path(path):
    """Return a stable absolute path for queue file comparisons."""
    return os.path.normpath(os.path.abspath(path))


def parse_job_name_from_command_line(command_line):
    """Extract Abaqus job name from a process command line."""
    if not command_line:
        return ""

    for pattern in JOB_NAME_COMMAND_PATTERNS:
        match = pattern.search(command_line)
        if match:
            return os.path.splitext(os.path.basename(match.group(1)))[0]

    return ""


def find_process_abaqus_job_name(process_row, process_by_pid):
    """Trace process parents until a command line containing job/input is found."""
    try:
        current_pid = int(process_row.get("ProcessId") or 0)
    except (TypeError, ValueError):
        return ""

    visited = set()

    while current_pid and current_pid in process_by_pid:
        if current_pid in visited:
            break

        visited.add(current_pid)
        current_process = process_by_pid[current_pid]
        job_name = parse_job_name_from_command_line(current_process.get("CommandLine") or "")
        if job_name:
            return job_name

        try:
            current_pid = int(current_process.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            break

    return ""


def join_process_command_line(cmdline):
    """Return a readable command line from psutil cmdline data."""
    if isinstance(cmdline, (list, tuple)):
        return " ".join(str(part) for part in cmdline)

    return str(cmdline or "")


def is_abaqus_process_name(process_name):
    """Return True when a process name is specific enough to be Abaqus-related."""
    stem = os.path.splitext(os.path.basename(str(process_name or "").lower()))[0]
    if not stem:
        return False

    if stem in ABAQUS_PROCESS_EXACT_NAMES:
        return True

    if stem.startswith("sma"):
        return True

    return any(marker in stem for marker in ABAQUS_PROCESS_SUBSTRINGS)


def is_detail_candidate_name(process_name):
    """Return True when a process name deserves expensive psutil details."""
    stem = normalize_process_name(process_name)
    if not stem:
        return False
    if stem in DETAIL_PROCESS_EXACT_NAMES:
        return True
    if stem in GENERIC_PROCESS_NAMES_REQUIRING_CMDLINE:
        return False
    return any(stem.startswith(prefix) for prefix in DETAIL_PROCESS_PREFIXES)


def is_generic_detail_candidate_name(process_name):
    """Return True for common launchers that need cmdline confirmation."""
    return normalize_process_name(process_name) in GENERIC_PROCESS_NAMES_REQUIRING_CMDLINE


def is_abaqus_command_line(command_line):
    """Return True when a generic launcher command line looks Abaqus-related."""
    text = str(command_line or "").lower()
    return any(marker in text for marker in ABAQUS_COMMAND_LINE_MARKERS)


def normalize_process_name(name: str) -> str:
    """Return a lowercase process name without path or extension."""
    return os.path.splitext(os.path.basename(str(name or "").strip().lower()))[0]


def is_active_solver_process(name: str, cmdline: str = "") -> bool:
    """Return True for real Abaqus solver processes, not launch helpers."""
    stem = normalize_process_name(name)
    command_text = str(cmdline or "").lower()
    if not stem and not command_text:
        return False

    helper_names = {
        "abaqus",
        "abq",
        "cmd",
        "conhost",
        "powershell",
        "pwsh",
        "mpiexec",
        "pre",
        "package",
        "launcher",
        "smalauncher",
    }
    if stem in helper_names:
        return False

    if stem in {"standard", "explicit"}:
        return True
    if stem.startswith(("standard", "explicit")):
        return True
    if stem.startswith("sma") and stem not in {"smalauncher"}:
        return True
    if "solver" in stem or "solver" in command_text:
        return True
    if "run standard" in command_text or "standard.exe" in command_text:
        return True
    if "run explicit" in command_text or "explicit.exe" in command_text:
        return True
    return False


def is_related_abaqus_process(name: str, cmdline: str = "") -> bool:
    """Return True for Abaqus-related processes used to resolve a job chain."""
    stem = normalize_process_name(name)
    command_text = str(cmdline or "").lower()
    if not stem and not command_text:
        return False
    if is_active_solver_process(stem, command_text):
        return True
    if stem in {"abaqus", "abq", "mpiexec", "pre", "package", "smalauncher"}:
        return True
    if stem.startswith("sma"):
        return True
    return any(marker in stem or marker in command_text for marker in ABAQUS_PROCESS_NAME_MARKERS)


def get_command_parameter_patterns(parameter):
    """Return cached regexes for extracting Abaqus command parameters."""
    parameter_key = parameter.lower()
    cached = COMMAND_PARAMETER_PATTERN_CACHE.get(parameter_key)
    if cached is not None:
        return cached

    name = re.escape(parameter_key)
    patterns = [
        re.compile(rf'(?:^|\s)-?{name}\s*=\s*"([^"]+)"', re.IGNORECASE),
        re.compile(rf"(?:^|\s)-?{name}\s*=\s*'([^']+)'", re.IGNORECASE),
        re.compile(rf"(?:^|\s)-?{name}\s*=\s*([^\s]+)", re.IGNORECASE),
        re.compile(rf'(?:^|\s)-{name}\s+"([^"]+)"', re.IGNORECASE),
        re.compile(rf"(?:^|\s)-{name}\s+'([^']+)'", re.IGNORECASE),
        re.compile(rf"(?:^|\s)-{name}\s+([^\s]+)", re.IGNORECASE),
    ]
    if parameter_key == "job":
        patterns.extend(
            [
                re.compile(r'(?:^|\s)-job\s+"([^"]+)"', re.IGNORECASE),
                re.compile(r"(?:^|\s)-job\s+'([^']+)'", re.IGNORECASE),
                re.compile(r"(?:^|\s)-job\s+([^\s]+)", re.IGNORECASE),
            ]
        )

    COMMAND_PARAMETER_PATTERN_CACHE[parameter_key] = patterns
    return patterns


def extract_abaqus_command_parameter(command_line, parameter):
    """Extract one Abaqus command parameter from a command line."""
    if not command_line or not parameter:
        return ""

    for pattern in get_command_parameter_patterns(parameter):
        match = pattern.search(command_line)
        if match:
            return match.group(1).strip()

    return ""


def get_process_and_parent_chain(process_row, process_by_pid, max_depth=8):
    """Return current process plus parents, limited to a small depth."""
    chain = []
    try:
        current_pid = int(process_row.get("ProcessId") or 0)
    except (TypeError, ValueError):
        return chain

    visited = set()
    while current_pid and current_pid in process_by_pid and current_pid not in visited:
        visited.add(current_pid)
        current_row = process_by_pid[current_pid]
        chain.append(current_row)
        if len(chain) >= max_depth:
            break
        try:
            current_pid = int(current_row.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            break

    return chain


def is_possible_abaqus_process(process_chain):
    """Return True if a process chain looks Abaqus-related."""
    for row in process_chain:
        if is_abaqus_process_name(row.get("Name", "")):
            return True

        command_line = str(row.get("CommandLine", "")).lower()
        if command_line and any(marker in command_line for marker in ABAQUS_PROCESS_NAME_MARKERS):
            return True

    return False


def get_first_chain_parameter(process_chain, parameter):
    """Find one Abaqus command parameter from current process or parents."""
    for row in process_chain:
        value = extract_abaqus_command_parameter(row.get("CommandLine") or "", parameter)
        if value:
            return value

    return ""


def scan_root_child_depth(work_dir, scan_root_dir):
    """Return depth under scan_root_dir, or None when work_dir is outside it."""
    normalized_root = normalize_work_dir(scan_root_dir)
    normalized_work_dir = normalize_work_dir(work_dir)
    if not normalized_root or not normalized_work_dir:
        return None
    if normalized_work_dir == normalized_root:
        return 0
    try:
        common = os.path.commonpath([normalized_root, normalized_work_dir])
    except ValueError:
        return None
    if common != normalized_root:
        return None
    relative = os.path.relpath(normalized_work_dir, normalized_root)
    if relative in {"", "."}:
        return 0
    if relative.startswith(".."):
        return None
    return len([part for part in relative.split(os.sep) if part and part != "."])


def work_dir_matches_scan_root(work_dir, scan_root_dir, max_child_depth=0):
    """Return True when work_dir is scan_root_dir or an allowed child directory."""
    depth = scan_root_child_depth(work_dir, scan_root_dir)
    return depth is not None and depth <= max(0, int(max_child_depth or 0))


def get_chain_work_dir(process_chain, target_work_dir, max_child_depth=0):
    """Return the matching process work directory from a process chain."""
    for row in process_chain:
        cwd = row.get("Cwd") or ""
        if cwd and work_dir_matches_scan_root(cwd, target_work_dir, max_child_depth):
            return os.path.abspath(os.path.normpath(cwd))

        command_line = row.get("CommandLine") or ""
        for parameter in ("indir", "outdir"):
            command_dir = extract_abaqus_command_parameter(command_line, parameter)
            if not command_dir:
                continue

            command_dir = command_dir.strip().strip('"').strip("'")
            if not os.path.isabs(command_dir) and cwd:
                command_dir = os.path.join(cwd, command_dir)

            if work_dir_matches_scan_root(command_dir, target_work_dir, max_child_depth):
                return os.path.abspath(os.path.normpath(command_dir))

    return ""


def resolve_external_job_path(value, work_dir, extension=""):
    """Resolve a command-line file path relative to an Abaqus work directory."""
    if not value:
        return ""

    value = value.strip().strip('"').strip("'")
    if extension and not os.path.splitext(value)[1]:
        value = value + extension

    if os.path.isabs(value):
        return normalize_joblist_path(value)

    return normalize_joblist_path(os.path.join(work_dir, value))


def oldjob_name_from_command_value(value):
    """Return the oldjob stem from a raw Abaqus oldjob= command value."""
    if not value:
        return ""
    value = value.strip().strip('"').strip("'")
    return os.path.splitext(os.path.basename(value))[0]


def detect_external_job_type(process_chain):
    """Infer Standard/Explicit/Abaqus from related process names and command lines."""
    chain_text = " ".join(f"{row.get('Name', '')} {row.get('CommandLine', '')}" for row in process_chain).lower()
    if "explicit" in chain_text:
        return "Explicit"
    if "standard" in chain_text:
        return "Standard"

    return "Abaqus"


def get_job_lock_info(work_dir, job_name):
    """Return lock-file presence and age for one Abaqus job."""
    if not work_dir or not job_name:
        return False, None

    lck_path = os.path.join(work_dir, job_name + ".lck")
    if not os.path.exists(lck_path):
        return False, None

    try:
        return True, max(0.0, time.time() - os.path.getmtime(lck_path))
    except OSError:
        return True, None


def _diagnostics_status_is_completed(status):
    return str(status or "").lower() in {"完成", "已完成", "completed", STATUS_COMPLETED.lower()}


def _diagnostics_status_is_terminated(status, detail=""):
    text = f"{status} {detail}".lower()
    return any(marker in text for marker in ("终止", "已终止", "terminated", "aborted"))


def _diagnostics_status_is_failed(status):
    return str(status or "").lower() in {"失败", "运行失败", "failed", STATUS_FAILED.lower()}


def classify_external_job_runtime(
    *,
    job_name: str,
    work_dir: str,
    process_names: list[str],
    process_cmdlines: list[str] | None = None,
    diagnostics_status: str = "",
    diagnostics_detail: str = "",
    stale_lock_grace_seconds: int = STALE_LOCK_GRACE_SECONDS,
) -> dict:
    """Classify one external Abaqus job from processes, LCK and diagnostics."""
    process_cmdlines = process_cmdlines or []
    pairs = list(zip(process_names, process_cmdlines + [""] * len(process_names)))
    has_solver_process = any(is_active_solver_process(name, cmdline) for name, cmdline in pairs)
    has_related_process = any(is_related_abaqus_process(name, cmdline) for name, cmdline in pairs)
    lock_exists, lock_age_seconds = get_job_lock_info(work_dir, job_name)

    if has_solver_process:
        status = STATUS_RUNNING
        message = "检测到有效 Abaqus 求解进程"
    elif (
        has_related_process
        and lock_exists
        and (lock_age_seconds is None or lock_age_seconds < stale_lock_grace_seconds)
    ):
        status = STATUS_CONFIRMING
        message = "检测到 Abaqus 相关进程和较新的 LCK 文件，暂未检测到有效求解进程"
    elif has_related_process and not lock_exists:
        status = STATUS_STARTING
        message = "检测到 Abaqus 相关进程，尚未发现 LCK 文件"
    elif lock_exists and (lock_age_seconds is None or lock_age_seconds >= stale_lock_grace_seconds):
        status = STATUS_INTERRUPTED
        message = "存在残留 LCK 文件，但未检测到有效求解进程"
    elif _diagnostics_status_is_completed(diagnostics_status):
        status = STATUS_COMPLETED
        message = diagnostics_detail or "根据诊断文件判定作业已经完成"
    elif _diagnostics_status_is_terminated(diagnostics_status, diagnostics_detail):
        status = STATUS_TERMINATED
        message = diagnostics_detail or "根据诊断文件判定作业已经终止"
    elif _diagnostics_status_is_failed(diagnostics_status):
        status = STATUS_FAILED
        message = diagnostics_detail or "根据诊断文件判定作业运行失败"
    else:
        status = STATUS_UNKNOWN
        message = "未检测到有效求解进程，也未发现可确认结束状态的诊断信息"

    return {
        "status": status,
        "message": message,
        "lock_exists": lock_exists,
        "lock_age_seconds": lock_age_seconds,
        "has_solver_process": has_solver_process,
        "has_related_process": has_related_process,
    }


def get_psutil_process_snapshot(force=False, include_details=False):
    """Return a cached psutil process snapshot with optional Abaqus-only details."""
    probe_start = time.monotonic()
    if psutil is None:
        return []

    now = time.monotonic()
    cache_key = bool(include_details)
    cache_entry = process_snapshot_cache[cache_key]
    cache_valid = (
        not force
        and now - cache_entry.get("timestamp", 0.0) < PROCESS_SNAPSHOT_CACHE_SECONDS
    )
    if cache_valid:
        log_performance(
            f"process snapshot cache hit; rows={len(cache_entry['rows'])}; details={cache_key}"
        )
        rows = cache_entry["rows"]
        hang_probe_log(
            "get_psutil_process_snapshot",
            time.monotonic() - probe_start,
            threshold=0.2,
            rows=len(rows),
            include_details=include_details,
            cache_hit=True,
            ttl=PROCESS_SNAPSHOT_CACHE_SECONDS,
        )
        return rows

    started_at = time.perf_counter()
    rows = []
    rows_by_pid = {}
    process_by_pid = {}
    detail_pids = set()
    candidate_pids = set()
    cmdline_checked = 0

    for process in psutil.process_iter(["pid", "ppid", "name"]):
        try:
            info = process.info
            pid = int(info.get("pid") or 0)
            row = {
                "Name": info.get("name") or "",
                "ProcessId": pid,
                "ParentProcessId": info.get("ppid") or 0,
                "CommandLine": "",
                "WorkingSetSize": 0,
                "PrivatePageCount": 0,
                "CreateTime": 0,
                "Cwd": "",
            }
            rows.append(row)
            rows_by_pid[pid] = row
            process_by_pid[pid] = process
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue

    children_by_parent = {}
    for row in rows:
        try:
            pid = int(row.get("ProcessId") or 0)
            parent_pid = int(row.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            continue
        if pid:
            children_by_parent.setdefault(parent_pid, []).append(pid)

    for row in rows:
        process_name = row.get("Name") or ""
        is_generic_candidate = is_generic_detail_candidate_name(process_name)
        if not is_detail_candidate_name(process_name) and not is_generic_candidate:
            continue

        current_pid = int(row.get("ProcessId") or 0)
        if is_generic_candidate:
            process = process_by_pid.get(current_pid)
            if process is None:
                continue
            try:
                command_line = join_process_command_line(process.cmdline())
                cmdline_checked += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                command_line = ""
            if not is_abaqus_command_line(command_line):
                continue
            row["CommandLine"] = command_line

        candidate_pids.add(current_pid)
        visited = set()
        while current_pid and current_pid in rows_by_pid and current_pid not in visited:
            visited.add(current_pid)
            detail_pids.add(current_pid)
            current_pid = int(rows_by_pid[current_pid].get("ParentProcessId") or 0)

    # Include descendants of Abaqus launchers/solvers, so helper processes that
    # do not have Abaqus-looking names still contribute to memory totals.
    pending_parent_pids = list(candidate_pids)
    while pending_parent_pids:
        parent_pid = pending_parent_pids.pop()
        for child_pid in children_by_parent.get(parent_pid, ()):
            if child_pid in detail_pids:
                continue
            detail_pids.add(child_pid)
            pending_parent_pids.append(child_pid)

    if not include_details:
        process_snapshot_cache[False].update(
            {"timestamp": now, "rows": rows}
        )
        log_performance(
            f"process snapshot light; rows={len(rows)}; abaqus_pids={len(detail_pids)}; "
            f"cmdline_checked={cmdline_checked}; elapsed={(time.perf_counter() - started_at) * 1000:.1f} ms"
        )
        hang_probe_log(
            "get_psutil_process_snapshot",
            time.monotonic() - probe_start,
            threshold=0.2,
            rows=len(rows),
            include_details=include_details,
            cache_hit=False,
            abaqus_pids=len(detail_pids),
            detail_checked=0,
            cmdline_checked=cmdline_checked,
            ttl=PROCESS_SNAPSHOT_CACHE_SECONDS,
        )
        return rows

    detail_checked = 0
    for pid in detail_pids:
        process = process_by_pid.get(pid)
        if process is None:
            continue
        row = rows_by_pid.get(pid)
        if row is None:
            continue
        detail_checked += 1
        try:
            memory_info = process.memory_info()
            row["WorkingSetSize"] = getattr(memory_info, "rss", 0)
            row["PrivatePageCount"] = getattr(memory_info, "private", 0)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            pass
        try:
            row["CreateTime"] = process.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            pass
        try:
            row["CommandLine"] = join_process_command_line(process.cmdline())
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            row["CommandLine"] = ""
        try:
            row["Cwd"] = process.cwd()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            row["Cwd"] = ""

    process_snapshot_cache[True].update(
        {"timestamp": now, "rows": rows}
    )
    log_performance(
        f"process snapshot full; rows={len(rows)}; abaqus_pids={len(detail_pids)}; "
        f"detail_checked={detail_checked}; cmdline_checked={cmdline_checked}; "
        f"elapsed={(time.perf_counter() - started_at) * 1000:.1f} ms"
    )
    hang_probe_log(
        "get_psutil_process_snapshot",
        time.monotonic() - probe_start,
        threshold=0.2,
            rows=len(rows),
            include_details=include_details,
            cache_hit=False,
            abaqus_pids=len(detail_pids),
            detail_checked=detail_checked,
            cmdline_checked=cmdline_checked,
            ttl=PROCESS_SNAPSHOT_CACHE_SECONDS,
        )
    return rows


def get_runtime_process_evidence(
    work_dir: str,
    job_name: str,
    *,
    process_rows: list[dict] | None = None,
    process_by_pid: dict[int, dict] | None = None,
    solver_rows: list[dict] | None = None,
    snapshot_available: bool = True,
    known_solver_pids: tuple[int, ...] | list[int] | set[int] = (),
) -> dict:
    """Return high-confidence solver processes for one internal job."""
    probe_start = time.monotonic()
    normalized_work_dir = normalize_work_dir(work_dir)
    normalized_job_name = str(job_name or "").strip().lower()
    if not normalized_work_dir or not normalized_job_name:
        result = {
            "active": False,
            "confidence": "",
            "pids": (),
            "solver_kind": "",
            "known_pid_active": False,
            "fallback_match_used": False,
        }
        hang_probe_log(
            "get_runtime_process_evidence",
            time.monotonic() - probe_start,
            threshold=0.2,
            job_name=job_name,
            active=False,
            reason="missing_key",
        )
        return result

    if not snapshot_available:
        result = {
            "active": True,
            "confidence": "snapshot_pending",
            "pids": (),
            "solver_kind": "",
            "known_pid_active": False,
            "fallback_match_used": False,
        }
        hang_probe_log(
            "get_runtime_process_evidence",
            time.monotonic() - probe_start,
            threshold=0.2,
            job_name=job_name,
            active=True,
            reason="snapshot_pending",
        )
        return result

    rows = process_rows if process_rows is not None else get_psutil_process_snapshot(force=False, include_details=True)
    if process_by_pid is None:
        process_by_pid = {}
        for row in rows:
            try:
                process_by_pid[int(row.get("ProcessId") or 0)] = row
            except (TypeError, ValueError):
                continue
    if solver_rows is None:
        solver_rows = [
            row
            for row in rows
            if is_active_solver_process(
                row.get("Name") or "",
                row.get("CommandLine") or "",
            )
        ]

    known_active_pids = []
    known_solver_kinds = set()
    for pid in known_solver_pids or ():
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        row = process_by_pid.get(pid)
        if row is None:
            continue
        process_name = row.get("Name") or ""
        command_line = row.get("CommandLine") or ""
        if not is_active_solver_process(process_name, command_line):
            continue
        chain = get_process_and_parent_chain(row, process_by_pid)
        chain_job_name = get_first_chain_parameter(chain, "job")
        if not chain_job_name:
            input_value = get_first_chain_parameter(chain, "input")
            chain_job_name = os.path.splitext(os.path.basename(input_value))[0] if input_value else ""
        if str(chain_job_name or "").strip().lower() != normalized_job_name:
            continue
        if not get_chain_work_dir(chain, work_dir):
            continue
        known_active_pids.append(pid)
        chain_text = " ".join(
            f"{item.get('Name', '')} {item.get('CommandLine', '')}"
            for item in chain
        ).lower()
        if "explicit" in chain_text:
            known_solver_kinds.add("explicit")
        elif "standard" in chain_text:
            known_solver_kinds.add("standard")

    if known_active_pids:
        solver_kind = ""
        if len(known_solver_kinds) == 1:
            solver_kind = next(iter(known_solver_kinds))
        result = {
            "active": True,
            "confidence": "high",
            "pids": tuple(sorted(set(known_active_pids))),
            "solver_kind": solver_kind,
            "known_pid_active": True,
            "fallback_match_used": False,
        }
        hang_probe_log(
            "get_runtime_process_evidence",
            time.monotonic() - probe_start,
            threshold=0.2,
            job_name=job_name,
            active=True,
            pids=len(result["pids"]),
            known_solver_pids=len(tuple(known_solver_pids or ())),
            known_pid_active=True,
            fallback_match_used=False,
        )
        return result

    matched_pids = []
    solver_kinds = set()
    for row in solver_rows:
        process_name = row.get("Name") or ""
        command_line = row.get("CommandLine") or ""

        chain = get_process_and_parent_chain(row, process_by_pid)
        chain_job_name = get_first_chain_parameter(chain, "job")
        if not chain_job_name:
            input_value = get_first_chain_parameter(chain, "input")
            chain_job_name = os.path.splitext(os.path.basename(input_value))[0] if input_value else ""
        if str(chain_job_name or "").strip().lower() != normalized_job_name:
            continue
        if not get_chain_work_dir(chain, work_dir):
            continue

        try:
            matched_pids.append(int(row.get("ProcessId") or 0))
        except (TypeError, ValueError):
            continue
        chain_text = " ".join(
            f"{item.get('Name', '')} {item.get('CommandLine', '')}"
            for item in chain
        ).lower()
        if "explicit" in chain_text:
            solver_kinds.add("explicit")
        elif "standard" in chain_text:
            solver_kinds.add("standard")

    solver_kind = ""
    if len(solver_kinds) == 1:
        solver_kind = next(iter(solver_kinds))
    result = {
        "active": bool(matched_pids),
        "confidence": "high" if matched_pids else "",
        "pids": tuple(sorted(set(matched_pids))),
        "solver_kind": solver_kind,
        "known_pid_active": False,
        "fallback_match_used": bool(known_solver_pids),
    }
    hang_probe_log(
        "get_runtime_process_evidence",
        time.monotonic() - probe_start,
        threshold=0.2,
        job_name=job_name,
        active=result["active"],
        pids=len(result["pids"]),
        known_solver_pids=len(tuple(known_solver_pids or ())),
        known_pid_active=False,
        fallback_match_used=bool(known_solver_pids),
    )
    return result


def fetch_psutil_process_rows_for_external_scan(force=True):
    """Read process data needed to import externally launched Abaqus jobs."""
    return get_psutil_process_snapshot(force=force, include_details=True)


def scan_running_abaqus_jobs_by_psutil(
    work_dir,
    force=True,
    known_external_jobs=None,
    process_rows=None,
):
    """Scan running Abaqus jobs under one scan root using psutil process data."""
    normalized_work_dir = normalize_work_dir(work_dir)
    known_external_jobs = known_external_jobs or []
    rows = (
        process_rows
        if process_rows is not None
        else get_psutil_process_snapshot(force=force, include_details=True)
    )
    process_by_pid = {}
    for row in rows:
        try:
            process_by_pid[int(row.get("ProcessId") or 0)] = row
        except (TypeError, ValueError):
            continue

    scanned_jobs = {}
    skipped_pids = []
    counted_pids = {}
    chain_cache = {}

    for row in rows:
        try:
            row_pid = int(row.get("ProcessId") or 0)
        except (TypeError, ValueError):
            row_pid = 0

        chain = chain_cache.get(row_pid)
        if chain is None:
            chain = get_process_and_parent_chain(row, process_by_pid)
            chain_cache[row_pid] = chain
        if not chain or not is_possible_abaqus_process(chain):
            continue

        matching_work_dir = get_chain_work_dir(
            chain,
            normalized_work_dir,
            max_child_depth=EXTERNAL_SCAN_MAX_CHILD_DEPTH,
        )
        if not matching_work_dir:
            continue

        job_value = get_first_chain_parameter(chain, "job")
        input_value = get_first_chain_parameter(chain, "input")
        job_name = os.path.splitext(os.path.basename(job_value))[0] if job_value else ""
        if not job_name and input_value:
            job_name = os.path.splitext(os.path.basename(input_value))[0]

        if not job_name:
            try:
                skipped_pids.append(row_pid)
            except (TypeError, ValueError):
                pass
            continue

        input_path = resolve_external_job_path(input_value, matching_work_dir, extension=".inp")
        if not input_path:
            candidate_inp = os.path.join(matching_work_dir, job_name + ".inp")
            if os.path.isfile(candidate_inp):
                input_path = normalize_joblist_path(candidate_inp)

        for_file = resolve_external_job_path(get_first_chain_parameter(chain, "user"), matching_work_dir)
        oldjob_value = get_first_chain_parameter(chain, "oldjob")
        oldjob_name = oldjob_name_from_command_value(oldjob_value)
        oldjob_path = resolve_external_job_path(oldjob_value, matching_work_dir, extension=".odb") if oldjob_value else ""
        cores = get_first_chain_parameter(chain, "cpus")
        memory_setting = get_first_chain_parameter(chain, "memory")
        job_key = (normalize_work_dir(matching_work_dir), job_name.lower())

        job_info = scanned_jobs.setdefault(
            job_key,
            {
                "job_name": job_name,
                "work_dir": matching_work_dir,
                "inp_path": input_path,
                "job_type": detect_external_job_type(chain),
                "restart_dependency": oldjob_name,
                "oldjob_path": oldjob_path,
                "for_file": for_file,
                "cores": cores,
                "memory_setting": memory_setting,
                "status": STATUS_RUNNING,
                "source": "external_psutil",
                "is_external": True,
                "pids": [],
                "pid_create_times": {},
                "rss_bytes": 0,
                "process_names": [],
                "process_cmdlines": [],
                "solver_pids": [],
                "related_pids": [],
            },
        )

        if input_path and not job_info.get("inp_path"):
            job_info["inp_path"] = input_path
        if for_file and not job_info.get("for_file"):
            job_info["for_file"] = for_file
        if oldjob_name and not job_info.get("restart_dependency"):
            job_info["restart_dependency"] = oldjob_name
        if oldjob_path and not job_info.get("oldjob_path"):
            job_info["oldjob_path"] = oldjob_path
        if cores and not job_info.get("cores"):
            job_info["cores"] = cores
        if memory_setting and not job_info.get("memory_setting"):
            job_info["memory_setting"] = memory_setting
        if job_info.get("job_type") == "Abaqus":
            job_info["job_type"] = detect_external_job_type(chain)

        if row_pid and row_pid not in counted_pids.setdefault(job_key, set()):
            counted_pids[job_key].add(row_pid)
            job_info["pids"].append(row_pid)
            job_info.setdefault("pid_create_times", {})[str(row_pid)] = row.get("CreateTime") or 0
            process_name = row.get("Name") or ""
            process_cmdline = row.get("CommandLine") or ""
            job_info["process_names"].append(process_name)
            job_info["process_cmdlines"].append(process_cmdline)
            if is_related_abaqus_process(process_name, process_cmdline):
                job_info["related_pids"].append(row_pid)
            if is_active_solver_process(process_name, process_cmdline):
                job_info["solver_pids"].append(row_pid)
            try:
                job_info["rss_bytes"] += int(row.get("WorkingSetSize") or 0)
            except (TypeError, ValueError):
                pass

    scanned_keys = set(scanned_jobs)
    for job_info in scanned_jobs.values():
        job_info["pids"] = sorted(job_info["pids"])
        job_info["solver_pids"] = sorted(set(job_info.get("solver_pids") or []))
        job_info["related_pids"] = sorted(set(job_info.get("related_pids") or []))
        runtime = classify_external_job_runtime(
            job_name=job_info.get("job_name", ""),
            work_dir=job_info.get("work_dir", ""),
            process_names=job_info.get("process_names") or [],
            process_cmdlines=job_info.get("process_cmdlines") or [],
        )
        job_info.update(
            {
                "runtime_status": runtime["status"],
                "runtime_message": runtime["message"],
                "lock_exists": runtime["lock_exists"],
                "lock_age_seconds": runtime["lock_age_seconds"],
                "has_solver_process": runtime["has_solver_process"],
                "has_related_process": runtime["has_related_process"],
            }
        )

    for known in known_external_jobs:
        known_job_name = known.get("job_name", "")
        known_work_dir = known.get("work_dir") or os.path.dirname(known.get("inp_path", ""))
        if not known_job_name or not work_dir_matches_scan_root(
            known_work_dir,
            normalized_work_dir,
            EXTERNAL_SCAN_MAX_CHILD_DEPTH,
        ):
            continue
        known_key = (normalize_work_dir(known_work_dir), known_job_name.lower())
        if known_key in scanned_keys:
            continue
        diagnostics_status, diagnostics_detail = inspect_job_files(known_work_dir, known_job_name)
        runtime = classify_external_job_runtime(
            job_name=known_job_name,
            work_dir=known_work_dir,
            process_names=[],
            process_cmdlines=[],
            diagnostics_status=diagnostics_status,
            diagnostics_detail=diagnostics_detail,
        )
        scanned_jobs[known_key] = {
            "item_id": known.get("item_id", ""),
            "job_name": known_job_name,
            "work_dir": normalize_joblist_path(known_work_dir),
            "inp_path": known.get("inp_path", ""),
            "source": "external_psutil",
            "is_external": bool(known.get("is_external", True)),
            "status_only_update": True,
            "pids": [],
            "pid_create_times": {},
            "rss_bytes": 0,
            "process_names": [],
            "process_cmdlines": [],
            "solver_pids": [],
            "related_pids": [],
            "runtime_status": runtime["status"],
            "runtime_message": runtime["message"],
            "lock_exists": runtime["lock_exists"],
            "lock_age_seconds": runtime["lock_age_seconds"],
            "has_solver_process": runtime["has_solver_process"],
            "has_related_process": runtime["has_related_process"],
            "diagnostics_status": diagnostics_status,
            "diagnostics_detail": diagnostics_detail,
        }

    return list(scanned_jobs.values()), skipped_pids


def fetch_psutil_process_rows():
    """Read process rows with psutil, avoiding PowerShell startup overhead."""
    return get_psutil_process_snapshot(force=False, include_details=True)


def fetch_windows_process_rows():
    """Read Windows process rows through PowerShell CIM."""
    if os.name != "nt":
        return []

    powershell_command = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "Get-CimInstance Win32_Process | "
        "Select-Object Name,ProcessId,ParentProcessId,CommandLine,WorkingSetSize,PrivatePageCount | "
        "ConvertTo-Json -Compress -Depth 3"
    )

    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                powershell_command,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if result.returncode != 0 or not result.stdout.strip():
        return []

    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    if isinstance(rows, dict):
        return [rows]

    if isinstance(rows, list):
        return rows

    return []


def build_abaqus_job_memory_usage(rows):
    """从共享进程快照构建按 Abaqus 作业分组的内存统计。"""
    process_by_pid = {}
    for row in rows:
        try:
            process_by_pid[int(row.get("ProcessId") or 0)] = row
        except (TypeError, ValueError):
            continue

    usage_by_job = {}

    for row in rows:
        job_name = find_process_abaqus_job_name(row, process_by_pid)
        if not job_name:
            continue

        try:
            working_set = int(row.get("WorkingSetSize") or 0)
        except (TypeError, ValueError):
            working_set = 0

        try:
            private_memory = int(row.get("PrivatePageCount") or 0)
        except (TypeError, ValueError):
            private_memory = 0

        usage = usage_by_job.setdefault(
            job_name,
            {
                "working_set": 0,
                "private_memory": 0,
                "process_count": 0,
                "process_names": set(),
            },
        )
        usage["working_set"] += working_set
        usage["private_memory"] += private_memory
        usage["process_count"] += 1
        if row.get("Name"):
            usage["process_names"].add(row["Name"])

    for usage in usage_by_job.values():
        usage["process_names"] = ", ".join(sorted(usage["process_names"]))

    return usage_by_job


def get_abaqus_job_memory_usage(force=False):
    """Return memory usage grouped by Abaqus job name."""
    now = time.monotonic()
    if not force and now - abaqus_memory_cache["timestamp"] < ABAQUS_MEMORY_POLL_INTERVAL_SECONDS:
        return abaqus_memory_cache["usage"]

    rows = fetch_psutil_process_rows()
    if not rows:
        rows = fetch_windows_process_rows()
    usage_by_job = build_abaqus_job_memory_usage(rows)

    abaqus_memory_cache["timestamp"] = now
    abaqus_memory_cache["usage"] = usage_by_job
    return usage_by_job


def get_cached_abaqus_job_memory_usage():
    """Return the latest memory usage cache without scanning processes."""
    return abaqus_memory_cache["usage"]


__all__ = [
    "normalize_joblist_path",
    "parse_job_name_from_command_line",
    "find_process_abaqus_job_name",
    "normalize_work_dir",
    "join_process_command_line",
    "is_abaqus_process_name",
    "normalize_process_name",
    "is_active_solver_process",
    "is_related_abaqus_process",
    "get_command_parameter_patterns",
    "extract_abaqus_command_parameter",
    "get_process_and_parent_chain",
    "is_possible_abaqus_process",
    "get_first_chain_parameter",
    "get_chain_work_dir",
    "resolve_external_job_path",
    "oldjob_name_from_command_value",
    "detect_external_job_type",
    "get_job_lock_info",
    "classify_external_job_runtime",
    "log_performance",
    "get_psutil_process_snapshot",
    "get_runtime_process_evidence",
    "fetch_psutil_process_rows_for_external_scan",
    "scan_running_abaqus_jobs_by_psutil",
    "fetch_psutil_process_rows",
    "fetch_windows_process_rows",
    "get_abaqus_job_memory_usage",
    "build_abaqus_job_memory_usage",
    "get_cached_abaqus_job_memory_usage",
]
