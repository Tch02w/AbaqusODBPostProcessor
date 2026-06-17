import ctypes
import os
import re


def get_physical_cpu_count():
    """Return the physical CPU core count, falling back to logical CPUs."""
    if os.name == "nt":
        try:
            relation_processor_core = 0
            returned_length = ctypes.c_uint32(0)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            get_processor_info = kernel32.GetLogicalProcessorInformationEx
            get_processor_info.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            get_processor_info.restype = ctypes.c_int

            get_processor_info(relation_processor_core, None, ctypes.byref(returned_length))
            if returned_length.value <= 0:
                raise OSError("CPU core information is unavailable.")

            buffer = ctypes.create_string_buffer(returned_length.value)
            success = get_processor_info(relation_processor_core, buffer, ctypes.byref(returned_length))
            if not success:
                raise OSError("Failed to read CPU core information.")

            offset = 0
            core_count = 0
            while offset < returned_length.value:
                entry_size = ctypes.c_uint32.from_buffer_copy(buffer, offset + 4).value
                if entry_size <= 0:
                    break
                core_count += 1
                offset += entry_size

            if core_count:
                return core_count
        except (OSError, AttributeError, ValueError):
            pass

    return os.cpu_count() or 1


MAX_CPUS = get_physical_cpu_count()
MAX_THREADS = os.cpu_count() or MAX_CPUS
DEFAULT_CPUS = max(1, MAX_CPUS // 2)
STA_POLL_INTERVAL_MS = 5000
RUNTIME_STATUS_INTERVAL_MS = 3000
OUTPUT_FLUSH_INTERVAL_MS = 150
ABAQUS_MEMORY_POLL_INTERVAL_SECONDS = 10
JOB_MEMORY_MONITOR_INTERVAL_MS = 15000
JOB_MEMORY_LEARNING_INTERVAL_MS = 15000
JOB_MEMORY_PATROL_INTERVAL_MS = 90000
EXTERNAL_JOB_MONITOR_INTERVAL_MS = 5000
STALE_LOCK_GRACE_SECONDS = 30
SOLVER_START_GRACE_SECONDS = 60
PROCESS_SNAPSHOT_CACHE_SECONDS = 5
FORMAL_QUEUE_SAVE_DEBOUNCE_MS = 500
MAX_JOB_LOG_LINES = 5000
MAX_HISTORY_LOG_LINES = 2000
LOG_TRIM_CHECK_INTERVAL = 50
ENABLE_PERFORMANCE_LOG = False
JOB_MEMORY_MIN_SAMPLES = 4
JOB_MEMORY_STABLE_POLLS = 4
JOB_MEMORY_STABLE_RELATIVE_DELTA = 0.08
JOB_MEMORY_BASE_SAFETY_FACTOR = 1.10
JOB_MEMORY_MAX_SAFETY_FACTOR = 1.50
JOB_MEMORY_SAFETY_FACTOR_STEP = 0.10
JOB_MEMORY_MAX_SAMPLES = 40
STA_FILE_ENCODING = "GBK"
LEFT_PANEL_WIDTH = 416
LOG_SEPARATOR_WIDTH = 64
LOG_TEXT_WIDTH = LOG_SEPARATOR_WIDTH
LOG_TEXT_PIXEL_WIDTH = 488
LOG_SCROLLBAR_WIDTH = 12
LOG_VIEW_PIXEL_WIDTH = LOG_TEXT_PIXEL_WIDTH + LOG_SCROLLBAR_WIDTH
RIGHT_PANEL_HORIZONTAL_PADDING = 0
RIGHT_PANEL_MIN_WIDTH = LOG_VIEW_PIXEL_WIDTH + RIGHT_PANEL_HORIZONTAL_PADDING
UNLIMITED_JOB_SLOTS = 10**9
WINDOW_HORIZONTAL_PADDING = 24
WINDOW_VERTICAL_PADDING = 24
WINDOW_HEIGHT = 720
PANEL_HEIGHT = WINDOW_HEIGHT - WINDOW_VERTICAL_PADDING
LEFT_ONLY_WIDTH = LEFT_PANEL_WIDTH + WINDOW_HORIZONTAL_PADDING
FULL_WIDTH = LEFT_PANEL_WIDTH + RIGHT_PANEL_MIN_WIDTH + WINDOW_HORIZONTAL_PADDING + 16
LEFT_ONLY_GEOMETRY = f"{LEFT_ONLY_WIDTH}x{WINDOW_HEIGHT}"
FULL_GEOMETRY = f"{FULL_WIDTH}x{WINDOW_HEIGHT}"
LEFT_ONLY_MIN_SIZE = (LEFT_ONLY_WIDTH, WINDOW_HEIGHT)
FULL_MIN_SIZE = (FULL_WIDTH, WINDOW_HEIGHT)
INP_FILE_PLACEHOLDER = "点击选择 INP 文件"
OLDJOB_PLACEHOLDER = "点击选择重启动 ODB（可选）"
FOR_FILE_PLACEHOLDER = "点击选择 Fortran 子程序（可选）"
COMPLETE_MARKERS = (
    "THE ANALYSIS HAS COMPLETED SUCCESSFULLY",
    "THE ANALYSIS HAS BEEN COMPLETED SUCCESSFULLY",
)

TERMINATE_MARKERS = (
    "THE ANALYSIS HAS BEEN TERMINATED",
    "TERMINATED",
    "USER REQUESTED TERMINATION",
)

ERROR_MARKERS = (
    "ABAQUS ERROR",
    "***ERROR",
    "ERROR:",
    "ABORTED",
    "EXITED WITH ERRORS",
    "EXITED WITH ERROR",
    "ABAQUS/ANALYSIS EXITED WITH ERROR",
    "THE ANALYSIS HAS NOT BEEN COMPLETED",
    "LICENSE ERROR",
    "LICENSE MANAGER ERROR",
    "UNABLE TO CHECKOUT",
    "NO LICENSE",
    "PROBLEM DURING COMPILATION",
    "PROBLEM DURING LINKING",
    "LINK FATAL ERROR",
    "TOO MANY ATTEMPTS",
    "NUMERICAL SINGULARITY",
    "ZERO PIVOT",
    "TIME INCREMENT REQUIRED IS LESS THAN",
    "EXCESSIVE DISTORTION",
    "DUE TO ERRORS",
    "ERRORS DETECTED",
)


def calculate_default_joblist_parallel(cpus=None):
    """Return default queue parallel count from 1.5x logical threads and per-job cores."""
    if cpus is None:
        cpus = DEFAULT_CPUS

    try:
        cpus = int(str(cpus).strip())
    except (TypeError, ValueError):
        cpus = DEFAULT_CPUS

    requested_cpus = MAX_CPUS if cpus == 0 else max(1, cpus)
    return max(1, (MAX_THREADS * 3) // (2 * requested_cpus))


JOB_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
DIAGNOSTIC_EXTENSIONS = (".sta", ".msg", ".dat", ".log")
OVERWRITE_PROMPT_MARKERS = (
    "OLD JOB FILES EXIST",
    "OVERWRITE?",
    "OVERWRITE",
    "ALREADY EXISTS",
    "EXISTING",
    "Y/N",
    "(Y/N)",
)
MEMORY_OPTIONS = ("默认", "%", "GB", "MB")
ABAQUS_PROCESS_NAME_MARKERS = (
    "abaqus",
    "standard",
    "explicit",
    "pre",
    "package",
    "sma",
    "solver",
    "mpiexec",
)
ABAQUS_PROCESS_EXACT_NAMES = {
    "abaqus",
    "standard",
    "explicit",
    "pre",
    "package",
    "packager",
    "mpiexec",
    "eqsequationsolver",
}
ABAQUS_PROCESS_SUBSTRINGS = (
    "abaqus",
    "standard",
    "explicit",
    "solver",
    "package",
)
JOB_NAME_COMMAND_PATTERNS = (
    re.compile(r'(?:^|\s)-?job\s*=\s*["\']?([^"\'\s]+)', re.IGNORECASE),
    re.compile(r'(?:^|\s)-job\s+["\']?([^"\'\s]+)', re.IGNORECASE),
    re.compile(r'(?:^|\s)-?input\s*=\s*["\']?([^"\'\s]+)', re.IGNORECASE),
)
COMMAND_PARAMETER_PATTERN_CACHE = {}

# ================= 字体统一设置 =================
FONT_FAMILY = "Microsoft YaHei"

FONT_TITLE = (FONT_FAMILY, 19, "bold")
FONT_SUBTITLE = (FONT_FAMILY, 9)
FONT_LABEL = (FONT_FAMILY, 12, "bold")
FONT_HINT = (FONT_FAMILY, 10)
FONT_ENTRY = (FONT_FAMILY, 10)
FONT_NUMERIC_ENTRY = (FONT_FAMILY, 12)
FONT_MEMORY_MENU = (FONT_FAMILY, 11)
FONT_BUTTON = (FONT_FAMILY, 12)
FONT_BUTTON_BOLD = (FONT_FAMILY, 13, "bold")
FONT_LOG = ("Consolas", 10)
FONT_JOB_SELECTOR = ("Consolas", 11)
FONT_QUEUE_BUTTON = (FONT_FAMILY, 13)
FONT_QUEUE_TABLE = (FONT_FAMILY, 10)
FONT_QUEUE_HEADING = (FONT_FAMILY, 10)

APP_BG = "#ffffff"
CARD_BG = "#ffffff"
LOG_BG = "#f9fafb"
JOBLIST_FILENAME = "joblist.json"
BTN_LIGHT_FG = "#dbe3ee"
BTN_LIGHT_HOVER = "#cbd5e1"
BTN_LIGHT_TEXT = "#111827"

BTN_PAUSE_FG = "#facc15"  # 黄色：暂停
BTN_PAUSE_HOVER = "#eab308"
BTN_RESUME_FG = "#22c55e"  # 绿色：继续
BTN_RESUME_HOVER = "#16a34a"
BTN_STATUS_TEXT_DARK = "#111827"
BTN_STATUS_TEXT_LIGHT = "#ffffff"

STATUS_PENDING_CONFIRM = "待确认"
STATUS_PENDING_RUN = "待运行"
STATUS_STARTING = "启动中"
STATUS_RUNNING = "运行中"
STATUS_COMPLETED = "已完成"
STATUS_FAILED = "运行失败"
STATUS_CANCELED = "已取消"
STATUS_TERMINATING = "正在终止"
STATUS_TERMINATED = "已终止"
STATUS_WAITING_DEPENDENCY = "等待前置"
STATUS_UNKNOWN = "状态未知"
STATUS_CONFIRMING = "状态确认中"
STATUS_INTERRUPTED = "疑似异常中断"
STATUS_DATACHECK_COMPLETED = "Datacheck Completed"
STATUS_DATACHECK_FAILED = "Datacheck Failed"

ACTIVE_STATUSES = frozenset(
    {
        STATUS_STARTING,
        STATUS_RUNNING,
        STATUS_CONFIRMING,
        STATUS_TERMINATING,
    }
)

TERMINAL_STATUSES = frozenset(
    {
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELED,
    STATUS_TERMINATED,
    STATUS_INTERRUPTED,
    STATUS_UNKNOWN,
    STATUS_DATACHECK_COMPLETED,
    STATUS_DATACHECK_FAILED,
    }
)


__all__ = [
    name
    for name in globals()
    if name.isupper() or name in {"get_physical_cpu_count", "calculate_default_joblist_parallel"}
]
