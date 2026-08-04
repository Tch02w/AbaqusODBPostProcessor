"""Human-friendly browser for generated ODB postprocessing results."""

from __future__ import annotations

import csv
import json
import os
import posixpath
import shutil
import sys
import threading
import traceback
import zipfile
from xml.etree import ElementTree
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QSize,
    QThread,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QDesktopServices,
    QImage,
    QImageReader,
    QMovie,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .naming import natural_sort_key
from .result_index import (
    INDEX_FILENAME,
    INDEX_SCHEMA_VERSION,
    ResultIndexCancelled,
    ResultIndexInvalid,
    build_result_index,
    index_path,
    load_result_index,
    update_result_index_scopes,
)
from .result_assets import (
    ASSET_MANIFEST_NAME,
    SHARED_DATA_ROOT_NAME,
    resolve_group_member_asset,
)


RESULT_ROOT_NAME = "AbaqusODBPostProcessor_Results"
PATH_ROLE = Qt.UserRole
NODE_KIND_ROLE = Qt.UserRole + 1
TIME_ROOT_ROLE = Qt.UserRole + 2


VARIABLE_LABELS = {
    "PILE_U_MAG": "桩身位移大小",
    "PILE_CON_S_MISES": "桩身混凝土 Mises 应力",
    "PILE_STEEL_S_MISES": "钢管/型钢 Mises 应力",
    "PILE_CON_DAMAGET": "桩身混凝土拉伸损伤",
    "PILE_CON_DAMAGEC": "桩身混凝土压缩损伤",
    "SOIL_PEEQ_XZ": "土体等效塑性应变切片",
    "SOIL_PEMAG_XZ": "土体塑性应变大小切片",
    "SOIL_S33_XZ": "土体竖向应力切片",
    "SOIL_S_MISES_XZ": "土体 Mises 应力切片",
    "REBAR_LONG_S_MISES_UNDEFORMED": "纵筋 Mises 应力",
    "REBAR_LONG_S11_UNDEFORMED": "纵筋轴向应力 S11",
}


FILE_DESCRIPTIONS = {
    "summary.xlsx": ("综合汇总", "推荐入口：可直接在 Excel 中查看主要数据表"),
    "timeline_alignment.csv": ("荷载与位移", "Step、帧号、增量与总时间的对照表"),
    "load_point_raw.csv": ("荷载与位移", "加载点的原始荷载和位移全过程"),
    "load_resistance_processed.csv": (
        "荷载分担",
        "推荐使用：桩顶反力、各根键承载力、根键合力与平均值、桩侧摩阻及平衡余量，统一为 kN",
    ),
    "contact_history_raw.csv": (
        "根键历程输出",
        "从 ODB History Output 对齐到加载帧的根键和桩—土接触原始有符号力，单位 N",
    ),
    "contact_history_sources.json": (
        "配置和说明",
        "根键与桩—土接触历程列对应的 Abaqus History Output 来源",
    ),
    "load_resistance_notes.json": (
        "荷载分担",
        "处理后荷载分担表的单位、符号约定、计算公式和使用注意事项",
    ),
    "load_resistance_sharing.png": (
        "荷载分担",
        "桩顶、各层根键合力、桩侧摩阻及其余承载分量曲线",
    ),
    "damage_scan.csv": ("模型状态与损伤", "损伤变量随正式加载过程的扫描结果"),
    "pile_total_axial_force_time_aligned.csv": (
        "桩轴力",
        "桩身总轴力：混凝土/钢管自由体力与纵筋轴力之和",
    ),
    "pile_total_force_moment_time_aligned.csv": (
        "桩弯矩",
        "桩身总弯矩沿深度分布，包含混凝土/钢管、纵筋及合计值",
    ),
    "pile_bending_moment_maxima.csv": (
        "桩弯矩",
        "各加载帧的最大弯矩及其所在深度",
    ),
    "concrete_axial_force_time_aligned.csv": (
        "桩轴力",
        "混凝土/钢管自由体轴力沿深度分布",
    ),
    "freebody_last_raw.csv": ("桩轴力", "自由体切片原始结果"),
    "rebar_element_stress_force_timehistory.csv": (
        "钢筋结果",
        "逐钢筋单元的应力、轴力及时间历程，文件通常较大",
    ),
    "rebar_force_by_element_level_timehistory.csv": (
        "钢筋结果",
        "按钢筋单元高度汇总的轴力时间历程",
    ),
    "rebar_metadata.json": ("钢筋结果", "钢筋材料、截面及空间范围说明"),
    "metadata.json": ("配置和说明", "本次 ODB、Step、帧和集合等提取说明"),
    "job_config.json": ("配置和说明", "本次提取使用的完整任务配置"),
    "host_postprocess_manifest.json": ("配置和说明", "宿主机后处理生成文件清单"),
    "abaqus_worker.log": ("运行日志", "该 ODB 的 Abaqus 提取日志"),
}


RECOMMENDED_NAMES = {
    "summary.xlsx",
    "load_point_raw.csv",
    "load_resistance_processed.csv",
    "load_resistance_sharing.png",
    "pile_total_axial_force_time_aligned.csv",
    "pile_total_force_moment_time_aligned.csv",
    "pile_bending_moment_maxima.csv",
}

SECTION_TABS = (
    ("common", "常用"),
    ("data", "数据"),
    ("plots", "曲线图"),
    ("contours", "云图"),
    ("animations", "动画"),
    ("info", "说明与日志"),
    ("all", "全部"),
)


@dataclass(frozen=True)
class ResultRecord:
    path: Path
    use_case: str
    description: str
    recommended: bool = False
    section: str = ""
    size: int = -1
    mtime_ns: int = 0
    error: str = ""
    internal: bool = False
    is_directory: bool = False


def _variable_from_path(path: Path) -> str:
    upper_parts = [part.upper() for part in path.parts]
    upper_stem = path.stem.upper()
    for variable in VARIABLE_LABELS:
        if variable in upper_parts or upper_stem.startswith(variable):
            return variable
    return ""


def classify_result(path: Path) -> ResultRecord:
    """Translate an internal result filename into a user-facing purpose."""

    name = path.name.casefold()
    if name in FILE_DESCRIPTIONS:
        use_case, description = FILE_DESCRIPTIONS[name]
        return ResultRecord(
            path,
            use_case,
            description,
            name in RECOMMENDED_NAMES,
        )

    suffix = path.suffix.casefold()
    parents = {part.casefold() for part in path.parts}
    variable = _variable_from_path(path)
    variable_label = VARIABLE_LABELS.get(variable, variable or "场变量")
    transparent = any(part.endswith("_transparent") for part in parents)

    if suffix == ".gif" or "animations" in parents:
        return ResultRecord(
            path,
            "动画",
            f"{variable_label}的完整正式加载过程动画",
            True,
        )
    if suffix == ".png" and "plots" in parents:
        if "load_resistance" in name:
            return ResultRecord(path, "荷载分担", "桩顶、根键、桩侧摩阻及其余承载分量曲线", True)
        if "axial_force" in name:
            return ResultRecord(path, "桩轴力", "桩身轴力沿深度分布曲线", True)
        if "bending_moment" in name:
            return ResultRecord(path, "桩弯矩", "桩身弯矩沿深度分布曲线", True)
        return ResultRecord(path, "曲线图", "后处理生成的结果曲线", True)
    if suffix == ".png" and (
        "frames" in parents
        or "contours" in parents
        or "frames_transparent" in parents
        or "contours_transparent" in parents
    ):
        kind = "透明背景云图" if transparent else "Abaqus 原始云图"
        return ResultRecord(path, "云图", f"{variable_label}；{kind}")
    if suffix == ".csv":
        if "rebar" in parents:
            return ResultRecord(path, "钢筋结果", "钢筋应力、内力或高度汇总数据")
        if "freebody" in parents:
            return ResultRecord(path, "桩轴力与弯矩", "自由体切片及桩身内力数据")
        if "data" in parents:
            return ResultRecord(path, "荷载与位移", "加载历程或帧对照数据")
        return ResultRecord(path, "其他表格数据", "可用 Excel 打开的 CSV 数据")
    if suffix == ".xlsx":
        return ResultRecord(path, "综合汇总", "可直接使用的 Excel 工作簿", True)
    if suffix in {".json", ".ndjson"}:
        return ResultRecord(path, "配置和说明", "配置、元数据或处理清单")
    if suffix in {".log", ".rpy", ".txt"}:
        return ResultRecord(path, "运行日志", "运行记录或诊断信息")
    if suffix in {".png", ".jpg", ".jpeg"}:
        return ResultRecord(path, "其他图片", "结果图片")
    return ResultRecord(path, "其他文件", "未分类的辅助文件")


def collect_result_records(scope: Path) -> list[ResultRecord]:
    if not scope.is_dir():
        return []
    records = [
        classify_result(path)
        for path in scope.rglob("*")
        if path.is_file()
    ]
    return sorted(
        records,
        key=lambda record: (
            not record.recommended,
            record.use_case.casefold(),
            record.path.name.casefold(),
            str(record.path).casefold(),
        ),
    )


def shared_assets_for_scope(scope: Path) -> list[Path]:
    """Resolve shared ODB data referenced by one group member or batch."""

    candidates: list[Path] = []
    direct = resolve_group_member_asset(scope)
    if direct is not None:
        candidates.append(direct)
    elif scope.is_dir():
        try:
            children = [path for path in scope.iterdir() if path.is_dir()]
        except OSError:
            children = []
        for child in children:
            resolved = resolve_group_member_asset(child)
            if resolved is not None:
                candidates.append(resolved)
    unique: dict[str, Path] = {}
    for candidate in candidates:
        unique[os.path.normcase(str(candidate.resolve()))] = candidate.resolve()
    return list(unique.values())


def collect_shared_asset_records(scope: Path) -> list[ResultRecord]:
    records: list[ResultRecord] = []
    seen: set[str] = set()
    for asset in shared_assets_for_scope(scope):
        for record in collect_result_records(asset):
            if record.path.name == ASSET_MANIFEST_NAME:
                continue
            key = os.path.normcase(str(record.path.resolve()))
            if key in seen:
                continue
            seen.add(key)
            try:
                stat = record.path.stat()
                size = stat.st_size
                mtime_ns = stat.st_mtime_ns
            except OSError:
                size = -1
                mtime_ns = 0
            records.append(
                ResultRecord(
                    record.path,
                    record.use_case,
                    record.description + "（ODB 公共数据）",
                    record.recommended,
                    result_section(record),
                    size,
                    mtime_ns,
                )
            )
    return records


def result_section(record: ResultRecord) -> str:
    """Return the primary horizontal-tab section for one result."""

    if record.section:
        return record.section
    suffix = record.path.suffix.casefold()
    parents = {part.casefold() for part in record.path.parts}
    if suffix == ".gif" or "animations" in parents:
        return "animations"
    if suffix in {".png", ".jpg", ".jpeg"} and "plots" in parents:
        return "plots"
    if suffix == ".png" and any(
        folder in parents
        for folder in (
            "frames",
            "contours",
            "frames_transparent",
            "contours_transparent",
        )
    ):
        return "contours"
    if suffix in {".csv", ".xlsx"}:
        return "data"
    if suffix in {".json", ".ndjson", ".log", ".rpy", ".txt"}:
        return "info"
    if suffix in {".png", ".jpg", ".jpeg"}:
        return "plots"
    return "other"


def record_matches_section(record: ResultRecord, section: str) -> bool:
    if section == "common":
        return record.recommended
    if section == "all":
        return True
    return result_section(record) == section


def format_file_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{size} B"


def fit_preview_size(source: QSize, available: QSize) -> QSize:
    if source.isEmpty() or available.isEmpty():
        return QSize()
    return source.scaled(available, Qt.KeepAspectRatio)


def preview_row_limit(path: Path) -> int:
    try:
        size = path.stat().st_size
    except OSError:
        return 12
    if size <= 1024 * 1024:
        return 50
    if size <= 20 * 1024 * 1024:
        return 25
    return 12


def read_csv_preview(
    path: Path, max_rows: int = 50
) -> tuple[list[str], list[list[str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader, [])
        rows = []
        for row_index, row in enumerate(reader):
            if row_index >= max_rows:
                break
            rows.append([str(value) for value in row])
    return [str(value) for value in header], rows


def _excel_column_index(reference: str) -> int:
    letters = ""
    for character in reference:
        if not character.isalpha():
            break
        letters += character.upper()
    index = 0
    for character in letters:
        index = index * 26 + ord(character) - ord("A") + 1
    return max(index - 1, 0)


def _excel_column_name(index: int) -> str:
    name = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def read_xlsx_preview(
    path: Path, max_rows: int = 50
) -> tuple[list[str], list[list[str]]]:
    """Read a small first-sheet preview using only the Python standard library."""

    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.findall(f"{{{main_ns}}}si"):
                shared_strings.append(
                    "".join(
                        node.text or ""
                        for node in item.iter()
                        if node.tag == f"{{{main_ns}}}t"
                    )
                )

        sheet_path = "xl/worksheets/sheet1.xml"
        if (
            "xl/workbook.xml" in archive.namelist()
            and "xl/_rels/workbook.xml.rels" in archive.namelist()
        ):
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            first_sheet = workbook.find(f".//{{{main_ns}}}sheet")
            relationships = ElementTree.fromstring(
                archive.read("xl/_rels/workbook.xml.rels")
            )
            if first_sheet is not None:
                relationship_id = first_sheet.attrib.get(f"{{{rel_ns}}}id", "")
                for relationship in relationships.findall(
                    f"{{{package_rel_ns}}}Relationship"
                ):
                    if relationship.attrib.get("Id") != relationship_id:
                        continue
                    target = relationship.attrib.get("Target", "")
                    sheet_path = (
                        target.lstrip("/")
                        if target.startswith("/")
                        else posixpath.normpath(posixpath.join("xl", target))
                    )
                    break

        worksheet = ElementTree.fromstring(archive.read(sheet_path))
        preview_cells: list[dict[int, str]] = []
        maximum_column = 0
        dimension = worksheet.find(f"{{{main_ns}}}dimension")
        if dimension is not None:
            final_reference = dimension.attrib.get("ref", "A1").split(":")[-1]
            maximum_column = _excel_column_index(final_reference)
        for row_node in worksheet.findall(
            f".//{{{main_ns}}}sheetData/{{{main_ns}}}row"
        ):
            if len(preview_cells) >= max_rows:
                break
            values: dict[int, str] = {}
            for cell in row_node.findall(f"{{{main_ns}}}c"):
                column = _excel_column_index(cell.attrib.get("r", "A1"))
                maximum_column = max(maximum_column, column)
                value_node = cell.find(f"{{{main_ns}}}v")
                cell_type = cell.attrib.get("t", "")
                if cell_type == "inlineStr":
                    value = "".join(
                        node.text or ""
                        for node in cell.iter()
                        if node.tag == f"{{{main_ns}}}t"
                    )
                else:
                    value = "" if value_node is None else value_node.text or ""
                    if cell_type == "s" and value:
                        value = shared_strings[int(value)]
                values[column] = value
            preview_cells.append(values)
    column_count = maximum_column + 1
    headers = [_excel_column_name(index) for index in range(column_count)]
    preview_rows = [
        [values.get(index, "") for index in range(column_count)]
        for values in preview_cells
    ]
    return headers, preview_rows


class ResultTableModel(QAbstractTableModel):
    """Virtual result table backed by lightweight record metadata."""

    headers = (
        "推荐",
        "数据用途",
        "内容说明",
        "文件名",
        "相对位置",
        "大小",
        "修改时间",
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.records: list[ResultRecord] = []
        self.scope = Path()

    def set_records(
        self, records: list[ResultRecord], scope: Path | str
    ) -> None:
        self.beginResetModel()
        self.records = list(records)
        self.scope = Path(scope)
        self.endResetModel()

    def rowCount(self, _parent=QModelIndex()) -> int:
        return len(self.records)

    def columnCount(self, _parent=QModelIndex()) -> int:
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if (
            role == Qt.DisplayRole
            and orientation == Qt.Horizontal
            and 0 <= section < len(self.headers)
        ):
            return self.headers[section]
        return super().headerData(section, orientation, role)

    def record_at(self, row: int) -> ResultRecord | None:
        if 0 <= row < len(self.records):
            return self.records[row]
        return None

    def _relative_path(self, record: ResultRecord) -> str:
        try:
            return str(record.path.relative_to(self.scope))
        except ValueError:
            return str(record.path)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self.record_at(index.row())
        if record is None:
            return None
        if role == PATH_ROLE:
            return str(record.path)
        if role == Qt.ToolTipRole:
            return record.error or str(record.path)
        if role == Qt.TextAlignmentRole and index.column() == 0:
            return int(Qt.AlignCenter)
        if role != Qt.DisplayRole:
            return None
        if index.column() == 0:
            return "★" if record.recommended else ""
        if index.column() == 1:
            return record.use_case
        if index.column() == 2:
            return record.description
        if index.column() == 3:
            return record.path.name
        if index.column() == 4:
            return self._relative_path(record)
        if index.column() == 5:
            return "不可用" if record.size < 0 else format_file_size(record.size)
        if index.column() == 6:
            if record.mtime_ns <= 0:
                return ""
            return datetime.fromtimestamp(
                record.mtime_ns / 1_000_000_000
            ).strftime("%Y-%m-%d %H:%M")
        return None

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        if not 0 <= column < len(self.headers):
            return

        def key(record: ResultRecord):
            values = (
                int(record.recommended),
                record.use_case.casefold(),
                record.description.casefold(),
                natural_sort_key(record.path.name),
                natural_sort_key(self._relative_path(record)),
                record.size,
                record.mtime_ns,
            )
            return values[column]

        self.layoutAboutToBeChanged.emit()
        self.records.sort(
            key=key,
            reverse=order == Qt.DescendingOrder,
        )
        self.layoutChanged.emit()


class _FunctionThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, function: Callable[[], Any], parent=None) -> None:
        super().__init__(parent)
        self.function = function

    def run(self) -> None:
        try:
            self.completed.emit(self.function())
        except Exception:
            self.failed.emit(traceback.format_exc())


class _IndexTaskThread(QThread):
    completed = Signal(object)
    cancelled = Signal()
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, task: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.task = task
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        path = Path(self.task["path"])
        operation = str(self.task["operation"])
        try:
            if operation == "incremental":
                result = update_result_index_scopes(
                    path,
                    self.task.get("scopes", []),
                    classify_result,
                    result_section,
                    self.cancel_event,
                    self.progress.emit,
                )
            else:
                result = build_result_index(
                    path,
                    classify_result,
                    result_section,
                    self.cancel_event,
                    self.progress.emit,
                )
            self.completed.emit(result)
        except ResultIndexCancelled:
            self.cancelled.emit()
        except Exception:
            self.failed.emit(traceback.format_exc())


def _time_directory_priority(path: Path) -> tuple[int, str]:
    prefix = path.name[:15]
    try:
        timestamp = int(datetime.strptime(prefix, "%Y%m%d_%H%M%S").timestamp())
    except ValueError:
        try:
            timestamp = int(path.stat().st_mtime)
        except OSError:
            timestamp = 0
    suffix = path.name[16:] if len(path.name) > 16 else ""
    return timestamp, suffix


class ResultIndexCoordinator(QObject):
    """One-at-a-time prioritized result-index queue shared by all browsers."""

    stateChanged = Signal(str, str, str)
    progressChanged = Signal(str, int, str)
    queueChanged = Signal()
    indexReady = Signal(str, object)
    logMessage = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.pending: list[dict[str, Any]] = []
        self.active: dict[str, Any] | None = None
        self.worker: _IndexTaskThread | None = None
        self.states: dict[str, tuple[str, str]] = {}
        self._sequence = 0
        self._dirty_scopes: dict[str, set[str]] = {}
        self._followup_refresh: set[str] = set()
        self._last_outcome: tuple[str, Any] | None = None

    @staticmethod
    def _key(path: Path | str) -> str:
        return str(Path(path).resolve())

    def has_work(self) -> bool:
        return self.active is not None or bool(self.pending)

    def state_for(self, path: Path | str) -> tuple[str, str]:
        return self.states.get(self._key(path), ("idle", ""))

    def active_path(self) -> str:
        return "" if self.active is None else str(self.active["path"])

    def pending_paths(self) -> list[str]:
        return [str(task["path"]) for task in self._sorted_pending()]

    def pending_ahead(self, path: Path | str) -> int:
        key = self._key(path)
        for index, task in enumerate(self._sorted_pending()):
            if task["path"] == key:
                return index
        return 0

    def _task_sort_key(self, task: dict[str, Any]) -> tuple[Any, ...]:
        if int(task["priority"]) >= 2:
            timestamp, suffix = _time_directory_priority(Path(task["path"]))
            suffix_priority = -int(suffix) if suffix.isdigit() else 0
            return (
                int(task["priority"]),
                -timestamp,
                suffix_priority,
                int(task["sequence"]),
            )
        return int(task["priority"]), int(task["sequence"])

    def _sorted_pending(self) -> list[dict[str, Any]]:
        return sorted(self.pending, key=self._task_sort_key)

    def _emit_pending_states(self) -> None:
        ordered = self._sorted_pending()
        for position, task in enumerate(ordered):
            detail = f"等待索引（前方有 {position} 个等待索引）"
            self.states[str(task["path"])] = ("waiting", detail)
            self.stateChanged.emit(str(task["path"]), "waiting", detail)
        self.queueChanged.emit()

    def _find_pending(self, key: str) -> dict[str, Any] | None:
        return next(
            (task for task in self.pending if task["path"] == key), None
        )

    def enqueue_manual(
        self, path: Path | str, *, refresh: bool = False
    ) -> bool:
        key = self._key(path)
        if refresh:
            if self.active is not None and self.active["path"] == key:
                self._followup_refresh.add(key)
                self.logMessage.emit(f"完整刷新将在当前任务后执行：{key}")
                return True
            existing = self._find_pending(key)
            if existing is not None:
                existing["operation"] = "refresh"
                existing["scopes"] = set()
                existing["priority"] = 0
                existing["sequence"] = self._next_sequence()
                self._emit_pending_states()
                self.logMessage.emit(f"索引任务已提升为完整刷新：{key}")
                return True
        return self._enqueue(
            key,
            operation="refresh" if refresh else "create",
            priority=0,
        )

    def enqueue_bulk(self, paths: list[Path | str]) -> int:
        added = 0
        for path in paths:
            resolved = Path(path).resolve()
            if index_path(resolved).is_file():
                continue
            added += int(
                self._enqueue(resolved, operation="create", priority=2)
            )
        return added

    def enqueue_incremental(
        self, time_directory: Path | str, scope: Path | str
    ) -> bool:
        key = self._key(time_directory)
        scope_key = self._key(scope)
        if self.active is not None and self.active["path"] == key:
            self._dirty_scopes.setdefault(key, set()).add(scope_key)
            return False
        pending = self._find_pending(key)
        if pending is not None:
            if pending["operation"] == "incremental":
                pending.setdefault("scopes", set()).add(scope_key)
            return False
        return self._enqueue(
            key,
            operation="incremental",
            priority=1,
            scopes={scope_key},
        )

    def _enqueue(
        self,
        path: Path | str,
        *,
        operation: str,
        priority: int,
        scopes: set[str] | None = None,
    ) -> bool:
        key = self._key(path)
        if self.active is not None and self.active["path"] == key:
            return False
        existing = self._find_pending(key)
        if existing is not None:
            if priority < int(existing["priority"]):
                existing["priority"] = priority
                existing["sequence"] = self._next_sequence()
            return False
        task = {
            "path": key,
            "operation": operation,
            "priority": priority,
            "sequence": self._next_sequence(),
            "scopes": set(scopes or ()),
        }
        self.pending.append(task)
        self.logMessage.emit(f"索引任务入队：{key}")
        self._emit_pending_states()
        self._start_next()
        return True

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _start_next(self) -> None:
        if self.active is not None or self.worker is not None or not self.pending:
            return
        ordered = self._sorted_pending()
        task = ordered[0]
        self.pending.remove(task)
        if isinstance(task.get("scopes"), set):
            task["scopes"] = sorted(task["scopes"])
        self.active = task
        detail = "索引中（已扫描 0 个文件）"
        self.states[str(task["path"])] = ("indexing", detail)
        self.stateChanged.emit(str(task["path"]), "indexing", detail)
        self._emit_pending_states()
        worker = _IndexTaskThread(task, self)
        self.worker = worker
        worker.progress.connect(self._progress)
        worker.completed.connect(
            lambda result: self._store_outcome("completed", result)
        )
        worker.cancelled.connect(
            lambda: self._store_outcome("cancelled", None)
        )
        worker.failed.connect(
            lambda details: self._store_outcome("failed", details)
        )
        worker.finished.connect(self._worker_finished)
        worker.start()

    def _progress(self, count: int, relative_path: str) -> None:
        if self.active is None:
            return
        key = str(self.active["path"])
        detail = f"索引中（已扫描 {count} 个文件）"
        self.states[key] = ("indexing", detail)
        self.stateChanged.emit(key, "indexing", detail)
        self.progressChanged.emit(key, count, relative_path)

    def _store_outcome(self, kind: str, payload: Any) -> None:
        self._last_outcome = (kind, payload)

    def _worker_finished(self) -> None:
        worker = self.worker
        task = self.active
        outcome = self._last_outcome or ("failed", "索引线程异常结束")
        self.worker = None
        self.active = None
        self._last_outcome = None
        if worker is not None:
            worker.deleteLater()
        if task is None:
            self._start_next()
            return
        key = str(task["path"])
        kind, payload = outcome
        if kind == "completed":
            detail = "索引完成"
            self.states[key] = ("complete", detail)
            self.stateChanged.emit(key, "complete", detail)
            self.indexReady.emit(key, payload)
            self.logMessage.emit(f"索引完成：{key}")
        elif kind == "cancelled":
            detail = "索引已取消"
            self.states[key] = ("cancelled", detail)
            self.stateChanged.emit(key, "cancelled", detail)
            self.logMessage.emit(f"索引已取消：{key}")
        else:
            last_line = str(payload).strip().splitlines()[-1]
            detail = f"索引失败：{last_line}"
            self.states[key] = ("failed", detail)
            self.stateChanged.emit(key, "failed", detail)
            self.logMessage.emit(detail)
        dirty = self._dirty_scopes.pop(key, set())
        if key in self._followup_refresh:
            self._followup_refresh.discard(key)
            self._enqueue(
                key,
                operation="refresh",
                priority=0,
            )
        elif dirty:
            self._enqueue(
                key,
                operation="incremental",
                priority=1,
                scopes=dirty,
            )
        self._emit_pending_states()
        self._start_next()

    def cancel_paths(
        self,
        paths: set[Path | str],
        *,
        include_active: bool = False,
    ) -> int:
        keys = {self._key(path) for path in paths}
        before = len(self.pending)
        removed = [task for task in self.pending if task["path"] in keys]
        self.pending = [
            task for task in self.pending if task["path"] not in keys
        ]
        for task in removed:
            key = str(task["path"])
            self.states[key] = ("idle", "未索引")
            self.stateChanged.emit(key, "idle", "未索引")
        if (
            include_active
            and self.active is not None
            and self.active["path"] in keys
            and self.worker is not None
        ):
            active_key = str(self.active["path"])
            self._dirty_scopes.pop(active_key, None)
            self._followup_refresh.discard(active_key)
            self.states[str(self.active["path"])] = (
                "cancelling",
                "正在安全取消当前索引",
            )
            self.stateChanged.emit(
                str(self.active["path"]),
                "cancelling",
                "正在安全取消当前索引",
            )
            self.worker.cancel()
        self._emit_pending_states()
        return before - len(self.pending)

    def cancel_all(self) -> None:
        self._dirty_scopes.clear()
        self._followup_refresh.clear()
        self.cancel_paths(
            {task["path"] for task in self.pending},
            include_active=False,
        )
        if self.worker is not None:
            self.worker.cancel()

    def shutdown(self, timeout_ms: int = 30000) -> bool:
        self.cancel_all()
        worker = self.worker
        if worker is None:
            return True
        return bool(worker.wait(timeout_ms))


class AspectRatioPreviewLabel(QLabel):
    """Preview images and GIFs without ever stretching their aspect ratio."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._source_pixmap = QPixmap()
        self._movie: QMovie | None = None
        self._movie_source_size = QSize()
        self.setAlignment(Qt.AlignCenter)
        self.setScaledContents(False)

    def clear_media(self) -> None:
        if self._movie is not None:
            self._movie.stop()
        self._movie = None
        self._movie_source_size = QSize()
        self._source_pixmap = QPixmap()
        self.clear()

    def show_text(self, text: str) -> None:
        self.clear_media()
        self.setText(text)

    def show_image(self, path: Path) -> None:
        self.clear_media()
        self._source_pixmap = QPixmap(str(path))
        self._fit_media()

    def show_qimage(self, image: QImage) -> None:
        self.clear_media()
        self._source_pixmap = QPixmap.fromImage(image)
        self._fit_media()

    def show_gif(
        self, path: Path, source_size: QSize | None = None
    ) -> QMovie:
        self.clear_media()
        movie = QMovie(str(path))
        movie.setCacheMode(QMovie.CacheNone)
        self._movie_source_size = source_size or QImageReader(str(path)).size()
        if self._movie_source_size.isEmpty():
            movie.jumpToFrame(0)
            self._movie_source_size = movie.currentPixmap().size()
        self._movie = movie
        self._fit_media()
        self.setMovie(movie)
        movie.start()
        return movie

    def _fit_media(self) -> None:
        available = self.contentsRect().size() - QSize(16, 16)
        if not self._source_pixmap.isNull():
            size = fit_preview_size(self._source_pixmap.size(), available)
            if not size.isEmpty():
                self.setPixmap(
                    self._source_pixmap.scaled(
                        size,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
        elif self._movie is not None and not self._movie_source_size.isEmpty():
            size = fit_preview_size(self._movie_source_size, available)
            if not size.isEmpty():
                self._movie.setScaledSize(size)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_media()


class ResultBrowserDialog(QDialog):
    """Browse existing result trees by engineering use rather than folder name."""

    def __init__(
        self,
        result_root: Path,
        parent=None,
        coordinator: ResultIndexCoordinator | None = None,
        *,
        prompt_for_unindexed: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self.setWindowModality(Qt.NonModal)
        self.setSizeGripEnabled(True)
        self.setWindowTitle("结果浏览器")
        self.resize(1480, 880)
        self.result_root = result_root.resolve()
        self.current_scope = self.result_root
        self.current_time_directory: Path | None = None
        self.records: list[ResultRecord] = []
        self.time_records: list[ResultRecord] = []
        self.visible_records: list[ResultRecord] = []
        self.index_metadata: dict[str, str] = {}
        self.preview_movie: QMovie | None = None
        self.prompt_for_unindexed = prompt_for_unindexed
        self.coordinator = coordinator or ResultIndexCoordinator(
            QApplication.instance()
        )
        self._owns_coordinator = coordinator is None
        self._background_threads: set[_FunctionThread] = set()
        self._direct_request = 0
        self._preview_request = 0
        self._build_ui()
        self.coordinator.stateChanged.connect(self._index_state_changed)
        self.coordinator.queueChanged.connect(self._queue_changed)
        self.coordinator.indexReady.connect(self._index_ready)
        self.coordinator.logMessage.connect(self._index_log_message)
        self._refresh_tree()

    def set_result_root(self, result_root: Path) -> None:
        """Switch to the result tree associated with the main window folder."""

        resolved = result_root.resolve()
        if resolved == self.result_root:
            self._refresh_tree()
            return
        self.result_root = resolved
        self.current_time_directory = None
        self.time_records = []
        self.records = []
        self.index_metadata = {}
        self.root_edit.setText(str(resolved))
        self._refresh_tree()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        root_row = QHBoxLayout()
        root_row.addWidget(QLabel("结果目录"))
        self.root_edit = QLineEdit(str(self.result_root))
        self.root_edit.returnPressed.connect(self._root_from_edit)
        root_row.addWidget(self.root_edit, 1)
        browse_button = QPushButton("浏览…")
        browse_button.clicked.connect(self._browse_root)
        root_row.addWidget(browse_button)
        self.refresh_structure_button = QPushButton("刷新目录结构")
        self.refresh_structure_button.clicked.connect(self._refresh_tree)
        root_row.addWidget(self.refresh_structure_button)
        layout.addLayout(root_row)

        hint = QLabel(
            "先在左侧选择对比组、批次或 ODB，再在右侧按“数据用途”查找；"
            "带 ★ 的文件是最适合直接使用的结果。"
        )
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        scope_panel = QWidget()
        scope_layout = QVBoxLayout(scope_panel)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        scope_layout.setSpacing(6)
        self.scope_tree = QTreeWidget()
        self.scope_tree.setHeaderLabel("结果范围")
        self.scope_tree.setMinimumWidth(310)
        self.scope_tree.itemSelectionChanged.connect(self._scope_changed)
        scope_layout.addWidget(self.scope_tree, 1)
        self.index_status_label = QLabel("请选择时间目录")
        self.index_status_label.setProperty("role", "hint")
        self.index_status_label.setWordWrap(True)
        scope_layout.addWidget(self.index_status_label)
        index_button_row = QHBoxLayout()
        self.cancel_index_button = QPushButton("取消等待索引")
        self.cancel_index_button.setEnabled(False)
        self.cancel_index_button.clicked.connect(self._cancel_selected_indexes)
        index_button_row.addWidget(self.cancel_index_button)
        self.create_all_indexes_button = QPushButton("创建全部时间索引")
        self.create_all_indexes_button.clicked.connect(
            self._create_all_indexes
        )
        index_button_row.addWidget(self.create_all_indexes_button)
        scope_layout.addLayout(index_button_row)
        splitter.addWidget(scope_panel)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self.section_tabs = QTabBar()
        self.section_tabs.setExpanding(False)
        self.section_tabs.setUsesScrollButtons(True)
        self.section_tabs.setElideMode(Qt.ElideRight)
        for key, label in SECTION_TABS:
            index = self.section_tabs.addTab(label)
            self.section_tabs.setTabData(index, key)
        self.section_tabs.currentChanged.connect(self._section_changed)
        right_layout.addWidget(self.section_tabs)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("具体内容"))
        self.use_case_combo = QComboBox()
        self.use_case_combo.setMinimumWidth(210)
        self.use_case_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.use_case_combo)
        filter_row.addWidget(QLabel("搜索"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入文件名、用途、说明或路径")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search_edit, 1)
        self.show_internal = QCheckBox("显示历史/内部记录")
        self.show_internal.toggled.connect(self._refresh_tree)
        filter_row.addWidget(self.show_internal)
        self.refresh_current_button = QPushButton("刷新当前批次")
        self.refresh_current_button.setEnabled(False)
        self.refresh_current_button.clicked.connect(self._refresh_current_index)
        filter_row.addWidget(self.refresh_current_button)
        right_layout.addLayout(filter_row)

        self.file_table = QTableView()
        self.table_model = ResultTableModel(self.file_table)
        self.file_table.setModel(self.table_model)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.file_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.file_table.setAlternatingRowColors(True)
        self.file_table.setSortingEnabled(True)
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.verticalHeader().setDefaultSectionSize(30)
        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.file_table.setColumnWidth(0, 48)
        self.file_table.setColumnWidth(1, 150)
        self.file_table.setColumnWidth(3, 230)
        self.file_table.setColumnWidth(5, 85)
        self.file_table.setColumnWidth(6, 140)
        self.file_table.sortByColumn(0, Qt.DescendingOrder)
        self.file_table.selectionModel().selectionChanged.connect(
            lambda _selected, _deselected: self._update_preview()
        )
        self.file_table.doubleClicked.connect(self._table_double_clicked)
        self.preview = AspectRatioPreviewLabel(
            "选择一个文件后，这里会显示它的用途和内容预览。"
        )
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.preview.setStyleSheet("QLabel { border: 1px solid palette(mid); padding: 8px; }")

        self.data_preview = QTableWidget()
        self.data_preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.data_preview.setSelectionMode(QAbstractItemView.NoSelection)
        self.data_preview.setAlternatingRowColors(True)
        self.data_preview.setWordWrap(False)
        self.data_preview.setToolTip("轻量预览：显示全部字段，行数根据文件大小自动控制")
        self.data_preview.verticalHeader().setDefaultSectionSize(28)
        self.data_preview.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )

        self.text_preview = QPlainTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setLineWrapMode(QPlainTextEdit.NoWrap)

        self.preview_stack = QStackedWidget()
        self.preview_stack.setMinimumHeight(120)
        self.preview_stack.addWidget(self.preview)
        self.preview_stack.addWidget(self.data_preview)
        self.preview_stack.addWidget(self.text_preview)

        self.content_splitter = QSplitter(Qt.Vertical)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.setHandleWidth(7)
        self.content_splitter.addWidget(self.file_table)
        self.content_splitter.addWidget(self.preview_stack)
        self.content_splitter.setSizes([560, 230])
        self.content_splitter.setStretchFactor(0, 3)
        self.content_splitter.setStretchFactor(1, 1)
        right_layout.addWidget(self.content_splitter, 1)

        action_row = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setProperty("role", "hint")
        action_row.addWidget(self.status_label, 1)
        self.open_button = QPushButton("打开文件")
        self.open_button.clicked.connect(self._open_selected)
        action_row.addWidget(self.open_button)
        self.folder_button = QPushButton("打开所在文件夹")
        self.folder_button.clicked.connect(self._open_selected_folder)
        action_row.addWidget(self.folder_button)
        self.copy_button = QPushButton("复制路径")
        self.copy_button.clicked.connect(self._copy_selected_paths)
        action_row.addWidget(self.copy_button)
        self.export_button = QPushButton("导出所选…")
        self.export_button.clicked.connect(self._export_selected)
        action_row.addWidget(self.export_button)
        right_layout.addLayout(action_row)

        splitter.addWidget(right)
        splitter.setSizes([330, 1150])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

    def _root_from_edit(self) -> None:
        self.result_root = Path(self.root_edit.text().strip()).resolve()
        self._refresh_tree()

    def _browse_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择结果目录", str(self.result_root)
        )
        if selected:
            self.result_root = Path(selected).resolve()
            self.root_edit.setText(str(self.result_root))
            self._refresh_tree()

    def _tree_item(
        self,
        parent,
        text: str,
        path: Path,
        kind: str,
        time_directory: Path | None = None,
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [text])
        item.setData(0, PATH_ROLE, str(path))
        item.setData(0, NODE_KIND_ROLE, kind)
        item.setData(
            0,
            TIME_ROOT_ROLE,
            "" if time_directory is None else str(time_directory),
        )
        item.setToolTip(0, str(path))
        return item

    def _refresh_tree(self) -> None:
        selected_path = ""
        selected = self.scope_tree.currentItem()
        if selected is not None:
            selected_path = str(selected.data(0, PATH_ROLE) or "")
        self.scope_tree.blockSignals(True)
        self.scope_tree.clear()
        root = self.result_root
        root_item = self._tree_item(
            self.scope_tree, "全部结果", root, "root"
        )
        restore_item = root_item
        include_internal = self.show_internal.isChecked()
        if root.is_dir():
            for group_dir in sorted(
                (path for path in root.iterdir() if path.is_dir()),
                key=lambda path: natural_sort_key(path.name),
            ):
                if group_dir.name.startswith("_") and not include_internal:
                    continue
                if group_dir.name == SHARED_DATA_ROOT_NAME:
                    shared_item = self._tree_item(
                        root_item,
                        "ODB 公共数据",
                        group_dir,
                        "shared_root",
                    )
                    for odb_data_dir in sorted(
                        (path for path in group_dir.iterdir() if path.is_dir()),
                        key=lambda path: natural_sort_key(path.name),
                    ):
                        odb_data_item = self._tree_item(
                            shared_item,
                            odb_data_dir.name,
                            odb_data_dir,
                            "shared_odb",
                        )
                        for asset_dir in sorted(
                            (
                                path
                                for path in odb_data_dir.iterdir()
                                if path.is_dir()
                            ),
                            key=lambda path: natural_sort_key(path.name),
                            reverse=True,
                        ):
                            asset_item = self._tree_item(
                                odb_data_item,
                                asset_dir.name,
                                asset_dir,
                                "shared_asset",
                            )
                            if str(asset_dir) == selected_path:
                                restore_item = asset_item
                    continue
                group_item = self._tree_item(
                    root_item, group_dir.name, group_dir, "group"
                )
                if str(group_dir) == selected_path:
                    restore_item = group_item
                for batch_dir in sorted(
                    (path for path in group_dir.iterdir() if path.is_dir()),
                    key=_time_directory_priority,
                    reverse=True,
                ):
                    batch_item = self._tree_item(
                        group_item,
                        batch_dir.name,
                        batch_dir,
                        "batch",
                        batch_dir,
                    )
                    if index_path(batch_dir).is_file():
                        batch_item.setToolTip(
                            0, f"{batch_dir}\n有索引（待验证）"
                        )
                    if str(batch_dir) == selected_path:
                        restore_item = batch_item
                    for odb_dir in sorted(
                        (path for path in batch_dir.iterdir() if path.is_dir()),
                        key=lambda path: natural_sort_key(path.name),
                    ):
                        odb_item = self._tree_item(
                            batch_item,
                            odb_dir.name,
                            odb_dir,
                            "odb",
                            batch_dir,
                        )
                        if str(odb_dir) == selected_path:
                            restore_item = odb_item
        root_item.setExpanded(True)
        self.scope_tree.setCurrentItem(restore_item)
        self.scope_tree.blockSignals(False)
        if restore_item is root_item:
            self._show_scope_placeholder(
                root, "请选择一个时间目录；启动阶段未读取任何结果索引。"
            )
            self._update_scope_index_status()
        else:
            self._scope_changed()
        self._refresh_index_controls()

    def _scope_changed(self) -> None:
        self._direct_request += 1
        self._preview_request += 1
        item = self.scope_tree.currentItem()
        if item is None:
            return
        path = Path(str(item.data(0, PATH_ROLE) or ""))
        kind = str(item.data(0, NODE_KIND_ROLE) or "")
        time_text = str(item.data(0, TIME_ROOT_ROLE) or "")
        time_directory = Path(time_text) if time_text else None
        if kind in {"root", "group", "shared_root", "shared_odb"}:
            self.current_time_directory = None
            self._show_scope_placeholder(
                path,
                "请选择时间目录或 ODB 公共数据版本。上级节点只显示目录结构。",
            )
            self._update_scope_index_status()
            self._refresh_index_controls()
            return
        if kind == "shared_asset":
            self.current_time_directory = None
            self.current_scope = path.resolve()
            self.records = [
                record
                for record in collect_result_records(self.current_scope)
                if record.path.name != ASSET_MANIFEST_NAME
            ]
            self.time_records = []
            self.index_metadata = {}
            self.index_status_label.setText("ODB 公共数据（无需对比组索引）")
            self._update_section_counts()
            self._refresh_use_case_options()
            self._apply_filter()
            self._refresh_index_controls()
            return
        if time_directory is None:
            return
        self._open_time_scope(time_directory, path)

    def _show_scope_placeholder(self, scope: Path, text: str) -> None:
        self.current_scope = scope
        self.records = []
        self.time_records = []
        self.visible_records = []
        self.index_metadata = {}
        self.table_model.set_records([], scope)
        self.status_label.setText(text)
        self.preview.show_text(text)
        self.preview_stack.setCurrentWidget(self.preview)
        self._update_section_counts()
        self._refresh_use_case_options()

    def _open_time_scope(
        self, time_directory: Path, scope: Path
    ) -> None:
        resolved_time = time_directory.resolve()
        if self.current_time_directory != resolved_time:
            self.time_records = []
            self.index_metadata = {}
        self.current_time_directory = resolved_time
        self.current_scope = scope.resolve()
        database = index_path(self.current_time_directory)
        if database.is_file():
            try:
                self._load_index_scope(self.current_time_directory, self.current_scope)
            except ResultIndexInvalid as error:
                self._show_scope_placeholder(
                    self.current_scope,
                    f"索引损坏、版本过旧或与目录不匹配：{error}",
                )
                self.index_status_label.setText(
                    f"索引需要刷新：{error}"
                )
            self._refresh_index_controls()
            return
        self.coordinator.enqueue_manual(self.current_time_directory)
        view_directly = not self.prompt_for_unindexed
        if self.prompt_for_unindexed:
            answer = QMessageBox.question(
                self,
                "文件尚未索引",
                "该时间目录尚未建立索引，已加入创建索引队列。\n"
                "是否仍要直接查看？直接查看只读取当前一级目录，"
                "不会递归扫描全部结果。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            view_directly = answer == QMessageBox.Yes
        if view_directly:
            self._start_direct_browse(self.current_scope)
        else:
            self._show_scope_placeholder(
                self.current_scope,
                "该时间目录正在等待索引；完成后将自动加载。",
            )
        self._refresh_index_controls()

    def _record_from_index(
        self, time_directory: Path, payload: dict[str, Any]
    ) -> ResultRecord:
        return ResultRecord(
            path=time_directory / str(payload["relative_path"]),
            use_case=str(payload["use_case"]),
            description=str(payload["description"]),
            recommended=bool(payload["recommended"]),
            section=str(payload["section"]),
            size=int(payload["size"]),
            mtime_ns=int(payload["mtime_ns"]),
            error=str(payload.get("error", "")),
        )

    def _load_index_scope(
        self, time_directory: Path, scope: Path
    ) -> None:
        if (
            self.current_time_directory != time_directory
            or not self.time_records
        ):
            loaded = load_result_index(time_directory)
            records = [
                self._record_from_index(time_directory, payload)
                for payload in loaded.records
            ]
            database = index_path(time_directory)
            try:
                stat = database.stat()
                size = stat.st_size
                mtime_ns = stat.st_mtime_ns
            except OSError:
                size = -1
                mtime_ns = 0
            records.append(
                ResultRecord(
                    database,
                    "内部索引",
                    "时间目录结果索引；"
                    f"schema={loaded.metadata.get('schema_version', '?')}，"
                    f"记录={loaded.metadata.get('record_count', len(records))}，"
                    f"刷新={loaded.metadata.get('updated_at', '未知')}",
                    section="info",
                    size=size,
                    mtime_ns=mtime_ns,
                    internal=True,
                )
            )
            self.time_records = records
            self.index_metadata = loaded.metadata
        self.current_scope = scope.resolve()
        if self.current_scope == time_directory.resolve():
            self.records = list(self.time_records)
        else:
            self.records = [
                record
                for record in self.time_records
                if record.path != index_path(time_directory)
                and record.path.is_relative_to(self.current_scope)
            ]
        self.records.extend(collect_shared_asset_records(self.current_scope))
        self.index_status_label.setText(
            f"索引完成｜{self.index_metadata.get('record_count', len(self.records))} 个文件"
        )
        self._update_section_counts()
        self._refresh_use_case_options()
        self._apply_filter()

    def _start_background(
        self,
        function: Callable[[], Any],
        completed: Callable[[Any], None],
        failed: Callable[[str], None] | None = None,
    ) -> None:
        thread = _FunctionThread(function)
        self._background_threads.add(thread)
        thread.completed.connect(completed)
        if failed is not None:
            thread.failed.connect(failed)

        def cleanup() -> None:
            self._background_threads.discard(thread)
            thread.deleteLater()

        thread.finished.connect(cleanup)
        thread.start()

    def _start_direct_browse(self, scope: Path) -> None:
        self._direct_request += 1
        request = self._direct_request
        target_scope = scope.resolve()
        self.current_scope = target_scope
        self.status_label.setText(f"正在读取当前目录：{self.current_scope}")
        self.table_model.set_records([], self.current_scope)

        def enumerate_one_level() -> list[ResultRecord]:
            records: list[ResultRecord] = []
            for entry in os.scandir(target_scope):
                path = Path(entry.path)
                is_junction = getattr(path, "is_junction", None)
                if path.is_symlink() or bool(
                    is_junction and is_junction()
                ):
                    continue
                if path.name in {INDEX_FILENAME} or path.name.startswith(
                    f"{INDEX_FILENAME}."
                ):
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                    is_directory = entry.is_dir(follow_symlinks=False)
                except OSError as error:
                    records.append(
                        ResultRecord(
                            path,
                            "无法读取的文件",
                            "读取当前目录时发生错误",
                            section="other",
                            error=str(error),
                        )
                    )
                    continue
                if is_directory:
                    records.append(
                        ResultRecord(
                            path,
                            "文件夹",
                            "未索引直接浏览；双击进入该目录",
                            True,
                            section="other",
                            size=0,
                            mtime_ns=stat.st_mtime_ns,
                            is_directory=True,
                        )
                    )
                elif entry.is_file(follow_symlinks=False):
                    classified = classify_result(path)
                    records.append(
                        ResultRecord(
                            path,
                            classified.use_case,
                            classified.description,
                            classified.recommended,
                            result_section(classified),
                            stat.st_size,
                            stat.st_mtime_ns,
                        )
                    )
            records.extend(collect_shared_asset_records(target_scope))
            return sorted(
                records,
                key=lambda record: (
                    not record.is_directory,
                    natural_sort_key(record.path.name),
                ),
            )

        def completed(records: list[ResultRecord]) -> None:
            if request != self._direct_request:
                return
            self.records = records
            self.time_records = []
            self.index_metadata = {}
            self._update_section_counts()
            self._refresh_use_case_options()
            self._apply_filter()
            self.status_label.setText(
                f"直接浏览：当前一级 {len(records)} 个项目；完整分类等待正式索引。"
            )

        def failed(details: str) -> None:
            if request != self._direct_request:
                return
            self._show_scope_placeholder(
                target_scope,
                f"当前目录读取失败：{details.strip().splitlines()[-1]}",
            )

        self._start_background(enumerate_one_level, completed, failed)

    def _table_double_clicked(self, index: QModelIndex) -> None:
        record = self.table_model.record_at(index.row())
        if record is not None and record.is_directory:
            self._start_direct_browse(record.path)
            return
        self._open_selected()

    def _time_directories_for_item(
        self, item: QTreeWidgetItem | None
    ) -> list[Path]:
        if item is None:
            return []
        kind = str(item.data(0, NODE_KIND_ROLE) or "")
        if kind in {"batch", "odb"}:
            value = str(item.data(0, TIME_ROOT_ROLE) or "")
            return [Path(value)] if value else []
        paths: list[Path] = []
        for index in range(item.childCount()):
            paths.extend(self._time_directories_for_item(item.child(index)))
        unique: dict[str, Path] = {}
        for path in paths:
            unique[str(path.resolve())] = path.resolve()
        return list(unique.values())

    def _bulk_scope_item(self) -> QTreeWidgetItem | None:
        item = self.scope_tree.currentItem()
        if item is None:
            return None
        kind = str(item.data(0, NODE_KIND_ROLE) or "")
        if kind == "odb":
            return item.parent().parent()
        if kind == "batch":
            return item.parent()
        return item

    def _create_all_indexes(self) -> None:
        paths = self._time_directories_for_item(self._bulk_scope_item())
        added = self.coordinator.enqueue_bulk(paths)
        self.index_status_label.setText(
            f"已加入 {added} 个无索引时间目录；已有或已排队目录已跳过。"
        )
        self._refresh_index_controls()

    def _refresh_current_index(self) -> None:
        if self.current_time_directory is None:
            return
        database = index_path(self.current_time_directory)
        if not database.is_file():
            return
        if self.coordinator.enqueue_manual(
            self.current_time_directory, refresh=True
        ):
            self.index_status_label.setText(
                "刷新当前批次已加入手动优先队列；旧索引仍可浏览。"
            )
        self._refresh_index_controls()

    def _cancel_selected_indexes(self) -> None:
        item = self.scope_tree.currentItem()
        paths = self._time_directories_for_item(item)
        if not paths:
            return
        kind = str(item.data(0, NODE_KIND_ROLE) or "")
        include_active = kind in {"batch", "odb"} and (
            self.coordinator.active_path()
            in {str(path.resolve()) for path in paths}
        )
        pending = set(self.coordinator.pending_paths())
        affected = {
            path
            for path in paths
            if str(path.resolve()) in pending
        }
        if len(affected) > 1:
            answer = QMessageBox.question(
                self,
                "取消等待索引",
                f"将取消当前范围内 {len(affected)} 个尚未开始的索引任务，是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.coordinator.cancel_paths(
            set(paths), include_active=include_active
        )
        self._refresh_index_controls()

    def _refresh_index_controls(self) -> None:
        item = self.scope_tree.currentItem()
        paths = self._time_directories_for_item(item)
        pending = set(self.coordinator.pending_paths())
        pending_count = sum(
            str(path.resolve()) in pending for path in paths
        )
        active = self.coordinator.active_path()
        active_selected = active and any(
            str(path.resolve()) == active for path in paths
        )
        kind = (
            str(item.data(0, NODE_KIND_ROLE) or "") if item is not None else ""
        )
        if active_selected and kind in {"batch", "odb"}:
            self.cancel_index_button.setText("取消当前索引")
            self.cancel_index_button.setEnabled(True)
        elif pending_count:
            label = (
                f"取消等待索引（{pending_count}）"
                if pending_count > 1
                else "取消等待索引"
            )
            self.cancel_index_button.setText(label)
            self.cancel_index_button.setEnabled(True)
        else:
            self.cancel_index_button.setText("取消等待索引")
            self.cancel_index_button.setEnabled(False)
        self.create_all_indexes_button.setEnabled(bool(paths))
        time_directory = self.current_time_directory
        time_state = (
            self.coordinator.state_for(time_directory)[0]
            if time_directory is not None
            else "idle"
        )
        self.refresh_current_button.setEnabled(
            time_directory is not None
            and index_path(time_directory).is_file()
            and time_state not in {"waiting", "indexing", "cancelling"}
        )

    def _update_scope_index_status(self) -> None:
        if self.current_time_directory is not None:
            return
        paths = self._time_directories_for_item(
            self.scope_tree.currentItem()
        )
        if not paths:
            self.index_status_label.setText("当前范围没有时间目录")
            return
        pending = set(self.coordinator.pending_paths())
        indexed = sum(index_path(path).is_file() for path in paths)
        waiting = sum(str(path.resolve()) in pending for path in paths)
        active = self.coordinator.active_path()
        indexing = sum(str(path.resolve()) == active for path in paths)
        self.index_status_label.setText(
            f"时间目录 {len(paths)} 个｜有索引（待验证）{indexed}｜"
            f"索引中 {indexing}｜等待索引 {waiting}"
        )

    def _index_state_changed(
        self, path: str, state: str, detail: str
    ) -> None:
        if (
            self.current_time_directory is not None
            and str(self.current_time_directory.resolve()) == path
        ):
            self.index_status_label.setText(detail)
        else:
            self._update_scope_index_status()
        self._refresh_index_controls()

    def _queue_changed(self) -> None:
        self._update_scope_index_status()
        self._refresh_index_controls()

    def _index_ready(self, path: str, _metadata: object) -> None:
        if (
            self.current_time_directory is None
            or str(self.current_time_directory.resolve()) != path
        ):
            return
        self._direct_request += 1
        self._preview_request += 1
        scope = self.current_scope
        self.time_records = []
        try:
            self._load_index_scope(self.current_time_directory, scope)
        except ResultIndexInvalid as error:
            self.index_status_label.setText(f"索引完成后校验失败：{error}")
        self._refresh_index_controls()

    def _index_log_message(self, text: str) -> None:
        if "失败" in text or "取消" in text:
            self.status_label.setText(text)

    def _section_key(self) -> str:
        index = self.section_tabs.currentIndex()
        return str(self.section_tabs.tabData(index) or "common")

    def _update_section_counts(self) -> None:
        for index, (key, label) in enumerate(SECTION_TABS):
            count = sum(
                record_matches_section(record, key) for record in self.records
            )
            self.section_tabs.setTabText(index, label)
            self.section_tabs.setTabToolTip(index, f"{label}：{count} 个文件")

    def _section_changed(self, _index: int) -> None:
        self._refresh_use_case_options()
        self._apply_filter()

    def _refresh_use_case_options(self) -> None:
        current_text = self.use_case_combo.currentText()
        section = self._section_key()
        use_cases = sorted(
            {
                record.use_case
                for record in self.records
                if record_matches_section(record, section)
            }
        )
        self.use_case_combo.blockSignals(True)
        self.use_case_combo.clear()
        self.use_case_combo.addItem("全部内容")
        self.use_case_combo.addItems(use_cases)
        index = self.use_case_combo.findText(current_text)
        self.use_case_combo.setCurrentIndex(index if index >= 0 else 0)
        self.use_case_combo.blockSignals(False)

    def _apply_filter(self) -> None:
        section = self._section_key()
        mode = self.use_case_combo.currentText()
        search = self.search_edit.text().strip().casefold()
        visible = []
        for record in self.records:
            if not record_matches_section(record, section):
                continue
            if mode != "全部内容" and record.use_case != mode:
                continue
            haystack = " ".join(
                (
                    record.use_case,
                    record.description,
                    record.path.name,
                    str(record.path),
                )
            ).casefold()
            if search and search not in haystack:
                continue
            visible.append(record)
        self.visible_records = visible
        self._populate_table()

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.current_scope))
        except ValueError:
            return str(path)

    def _populate_table(self) -> None:
        self.table_model.set_records(self.visible_records, self.current_scope)
        self.status_label.setText(
            f"当前范围 {len(self.records)} 个文件；筛选后 {len(self.visible_records)} 个"
        )
        self.preview_movie = None
        self.preview.show_text(
            "没有匹配结果。" if not self.visible_records else "选择一个文件查看说明或预览。"
        )
        self.preview_stack.setCurrentWidget(self.preview)

    def _selected_paths(self) -> list[Path]:
        paths = []
        selection = self.file_table.selectionModel()
        if selection is None:
            return paths
        for index in selection.selectedRows():
            record = self.table_model.record_at(index.row())
            if record is None:
                continue
            path = record.path
            if path and path not in paths:
                paths.append(path)
        return paths

    def _selected_record(self) -> ResultRecord | None:
        selection = self.file_table.selectionModel()
        if selection is None:
            return None
        rows = selection.selectedRows()
        if not rows:
            return None
        return self.table_model.record_at(rows[0].row())

    def _update_preview(self) -> None:
        record = self._selected_record()
        self._preview_request += 1
        request = self._preview_request
        self.preview_movie = None
        if record is None:
            self.preview.show_text("选择一个文件查看说明或预览。")
            self.preview_stack.setCurrentWidget(self.preview)
            return
        if record.is_directory:
            self.preview.show_text(
                f"文件夹：{record.path}\n\n双击后读取该目录的直属内容。"
            )
            self.preview_stack.setCurrentWidget(self.preview)
            return
        if record.internal:
            metadata = self.index_metadata
            self.text_preview.setPlainText(
                "\n".join(
                    (
                        "内部结果索引（只读）",
                        f"索引文件：{record.path}",
                        f"schema：{metadata.get('schema_version', INDEX_SCHEMA_VERSION)}",
                        f"绑定目录：{metadata.get('canonical_path', '')}",
                        f"记录数量：{metadata.get('record_count', '')}",
                        f"更新时间：{metadata.get('updated_at', '')}",
                        f"完整性标识：{metadata.get('identity_hash', '')}",
                    )
                )
            )
            self.preview_stack.setCurrentWidget(self.text_preview)
            return
        path = record.path
        suffix = path.suffix.casefold()
        self.preview.show_text("正在后台准备预览…")
        self.preview_stack.setCurrentWidget(self.preview)

        def prepare_preview() -> tuple[str, Any]:
            if suffix == ".gif":
                source_size = QImageReader(str(path)).size()
                if source_size.isEmpty():
                    raise OSError("GIF 无法读取")
                return "gif", (path, source_size)
            if suffix in {".png", ".jpg", ".jpeg"}:
                reader = QImageReader(str(path))
                image = reader.read()
                if image.isNull():
                    raise OSError(reader.errorString() or "图片无法读取")
                return "image", image
            if suffix in {".csv", ".xlsx"}:
                row_limit = preview_row_limit(path)
                if suffix == ".csv":
                    headers, rows = read_csv_preview(path, row_limit)
                else:
                    headers, rows = read_xlsx_preview(path, row_limit)
                return "table", (headers, rows, row_limit)
            return "text", self._text_preview(record)

        def completed(payload: tuple[str, Any]) -> None:
            if request != self._preview_request:
                return
            kind, value = payload
            if kind == "gif":
                gif_path, source_size = value
                self.preview_movie = self.preview.show_gif(
                    gif_path, source_size
                )
                self.preview_stack.setCurrentWidget(self.preview)
            elif kind == "image":
                self.preview.show_qimage(value)
                self.preview_stack.setCurrentWidget(self.preview)
            elif kind == "table":
                headers, rows, row_limit = value
                self._show_data_preview(headers, rows)
                self.data_preview.setToolTip(
                    f"轻量预览：已加载全部 {len(headers)} 列；"
                    f"当前最多读取前 {row_limit} 行"
                )
                self.preview_stack.setCurrentWidget(self.data_preview)
            else:
                self.text_preview.setPlainText(str(value))
                self.preview_stack.setCurrentWidget(self.text_preview)

        def failed(details: str) -> None:
            if request != self._preview_request:
                return
            self.text_preview.setPlainText(
                self._text_preview(record)
                + "\n\n预览失败："
                + details.strip().splitlines()[-1]
            )
            self.preview_stack.setCurrentWidget(self.text_preview)

        self._start_background(prepare_preview, completed, failed)

    def _show_data_preview(
        self, headers: list[str], rows: list[list[str]]
    ) -> None:
        column_count = max(
            len(headers),
            max((len(row) for row in rows), default=0),
        )
        self.data_preview.clear()
        self.data_preview.setColumnCount(column_count)
        self.data_preview.setRowCount(len(rows))
        if headers:
            padded_headers = headers + [
                str(index + 1) for index in range(len(headers), column_count)
            ]
            self.data_preview.setHorizontalHeaderLabels(padded_headers)
        for row_index, values in enumerate(rows):
            for column_index, value in enumerate(values):
                self.data_preview.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem(str(value)),
                )

    def _text_preview(self, record: ResultRecord) -> str:
        path = record.path
        lines = [
            f"数据用途：{record.use_case}",
            f"内容说明：{record.description}",
            f"文件位置：{path}",
        ]
        try:
            if path.suffix.casefold() == ".csv":
                with path.open("r", encoding="utf-8-sig", newline="") as stream:
                    reader = csv.reader(stream)
                    header = next(reader, [])
                    first_row = next(reader, [])
                lines.append(f"字段数量：{len(header)}")
                lines.append("字段：" + "、".join(header[:18]))
                if len(header) > 18:
                    lines.append("（字段较多，仅显示前 18 个）")
                if first_row:
                    lines.append("首行数据：" + " | ".join(first_row[:8]))
            elif path.suffix.casefold() in {".json", ".ndjson"}:
                text = path.read_text(encoding="utf-8", errors="replace")[:4000]
                try:
                    text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
                except json.JSONDecodeError:
                    pass
                lines.append("\n内容预览：\n" + text)
            elif path.suffix.casefold() == ".xlsx":
                lines.append("建议点击“打开文件”，直接在 Excel 中使用。")
            elif path.suffix.casefold() in {".log", ".txt", ".rpy"}:
                text = path.read_text(encoding="utf-8", errors="replace")[:4000]
                lines.append("\n内容预览：\n" + text)
        except (OSError, UnicodeError, StopIteration) as error:
            lines.append(f"预览失败：{error}")
        return "\n".join(lines)

    def _open_selected(self) -> None:
        record = self._selected_record()
        if record is None:
            QMessageBox.information(self, "尚未选择", "请先选择一个结果文件。")
            return
        if record.internal:
            QMessageBox.information(
                self,
                "内部索引只读",
                "结果索引不能从浏览器中打开或编辑。",
            )
            return
        if record.is_directory:
            self._start_direct_browse(record.path)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(record.path)))

    def _open_selected_folder(self) -> None:
        paths = self._selected_paths()
        target = paths[0].parent if paths else self.current_scope
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _copy_selected_paths(self) -> None:
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "尚未选择", "请先选择一个或多个结果文件。")
            return
        QApplication.clipboard().setText("\n".join(str(path) for path in paths))
        self.status_label.setText(f"已复制 {len(paths)} 个文件路径")

    def _export_selected(self) -> None:
        selection = self.file_table.selectionModel()
        records = (
            [
                self.table_model.record_at(index.row())
                for index in selection.selectedRows()
            ]
            if selection is not None
            else []
        )
        records = [
            record
            for record in records
            if record is not None
            and not record.internal
            and not record.is_directory
        ]
        if not records:
            QMessageBox.information(self, "尚未选择", "请先选择一个或多个结果文件。")
            return
        destination = QFileDialog.getExistingDirectory(
            self, "选择导出目录", str(self.current_scope)
        )
        if not destination:
            return
        destination_root = Path(destination)
        copied = 0
        for record in records:
            source = record.path
            try:
                relative = source.relative_to(self.current_scope)
            except ValueError:
                relative = Path(source.name)
            target = destination_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
        QMessageBox.information(
            self,
            "导出完成",
            f"已导出 {copied} 个文件到：\n{destination_root}",
        )

    def closeEvent(self, event) -> None:
        self._direct_request += 1
        self._preview_request += 1
        if self._owns_coordinator and not self.coordinator.shutdown(30000):
            QMessageBox.warning(
                self,
                "索引任务仍在退出",
                "后台索引尚未安全停止，请稍后再关闭结果浏览器。",
            )
            event.ignore()
            return
        super().closeEvent(event)


def main() -> int:
    from .ui_style import apply_application_style

    application = QApplication(sys.argv)
    apply_application_style(application)
    requested = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    result_root = (
        requested
        if requested.name == RESULT_ROOT_NAME
        else requested / RESULT_ROOT_NAME
    )
    window = ResultBrowserDialog(result_root)
    window.showMaximized()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
