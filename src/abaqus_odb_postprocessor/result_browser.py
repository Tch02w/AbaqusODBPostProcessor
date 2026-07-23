"""Human-friendly browser for generated ODB postprocessing results."""

from __future__ import annotations

import csv
import json
import posixpath
import shutil
import sys
import zipfile
from xml.etree import ElementTree
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImageReader, QMovie, QPixmap
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
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


RESULT_ROOT_NAME = "AbaqusODBPostProcessor_Results"
PATH_ROLE = Qt.UserRole


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


def result_section(record: ResultRecord) -> str:
    """Return the primary horizontal-tab section for one result."""

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

    def show_gif(self, path: Path) -> QMovie:
        self.clear_media()
        movie = QMovie(str(path))
        movie.setCacheMode(QMovie.CacheAll)
        self._movie_source_size = QImageReader(str(path)).size()
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

    def __init__(self, result_root: Path, parent=None) -> None:
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
        self.records: list[ResultRecord] = []
        self.visible_records: list[ResultRecord] = []
        self.preview_movie: QMovie | None = None
        self._build_ui()
        self._refresh_tree()

    def set_result_root(self, result_root: Path) -> None:
        """Switch to the result tree associated with the main window folder."""

        resolved = result_root.resolve()
        if resolved == self.result_root:
            self._refresh_tree()
            return
        self.result_root = resolved
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
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self._refresh_tree)
        root_row.addWidget(refresh_button)
        layout.addLayout(root_row)

        hint = QLabel(
            "先在左侧选择对比组、批次或 ODB，再在右侧按“数据用途”查找；"
            "带 ★ 的文件是最适合直接使用的结果。"
        )
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.scope_tree = QTreeWidget()
        self.scope_tree.setHeaderLabel("结果范围")
        self.scope_tree.setMinimumWidth(310)
        self.scope_tree.itemSelectionChanged.connect(self._scope_changed)
        splitter.addWidget(self.scope_tree)

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
        self.search_edit.setPlaceholderText("输入文件名、数据内容或路径")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search_edit, 1)
        self.show_internal = QCheckBox("显示历史/内部记录")
        self.show_internal.toggled.connect(self._refresh_tree)
        filter_row.addWidget(self.show_internal)
        right_layout.addLayout(filter_row)

        self.file_table = QTableWidget(0, 7)
        self.file_table.setHorizontalHeaderLabels(
            ["推荐", "数据用途", "内容说明", "文件名", "相对位置", "大小", "修改时间"]
        )
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.file_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.file_table.setAlternatingRowColors(True)
        self.file_table.setSortingEnabled(False)
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.verticalHeader().setDefaultSectionSize(30)
        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.file_table.itemSelectionChanged.connect(self._update_preview)
        self.file_table.itemDoubleClicked.connect(lambda _item: self._open_selected())
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
        open_button = QPushButton("打开文件")
        open_button.clicked.connect(self._open_selected)
        action_row.addWidget(open_button)
        folder_button = QPushButton("打开所在文件夹")
        folder_button.clicked.connect(self._open_selected_folder)
        action_row.addWidget(folder_button)
        copy_button = QPushButton("复制路径")
        copy_button.clicked.connect(self._copy_selected_paths)
        action_row.addWidget(copy_button)
        export_button = QPushButton("导出所选…")
        export_button.clicked.connect(self._export_selected)
        action_row.addWidget(export_button)
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

    def _tree_item(self, parent, text: str, path: Path) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [text])
        item.setData(0, PATH_ROLE, str(path))
        item.setToolTip(0, str(path))
        return item

    def _refresh_tree(self) -> None:
        self.scope_tree.blockSignals(True)
        self.scope_tree.clear()
        root = self.result_root
        root_item = self._tree_item(self.scope_tree, "全部结果", root)
        include_internal = self.show_internal.isChecked()
        if root.is_dir():
            for group_dir in sorted(
                (path for path in root.iterdir() if path.is_dir()),
                key=lambda path: path.name.casefold(),
            ):
                if group_dir.name.startswith("_") and not include_internal:
                    continue
                group_item = self._tree_item(root_item, group_dir.name, group_dir)
                for batch_dir in sorted(
                    (path for path in group_dir.iterdir() if path.is_dir()),
                    key=lambda path: path.name.casefold(),
                    reverse=True,
                ):
                    batch_item = self._tree_item(group_item, batch_dir.name, batch_dir)
                    for odb_dir in sorted(
                        (path for path in batch_dir.iterdir() if path.is_dir()),
                        key=lambda path: path.name.casefold(),
                    ):
                        self._tree_item(batch_item, odb_dir.name, odb_dir)
        root_item.setExpanded(True)
        self.scope_tree.setCurrentItem(root_item)
        self.scope_tree.blockSignals(False)
        self._load_scope(root)

    def _scope_changed(self) -> None:
        item = self.scope_tree.currentItem()
        if item is None:
            return
        path = Path(str(item.data(0, PATH_ROLE) or ""))
        self._load_scope(path)

    def _load_scope(self, scope: Path) -> None:
        self.current_scope = scope
        self.records = collect_result_records(scope)
        self._update_section_counts()
        self._refresh_use_case_options()
        self._apply_filter()

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
        self.file_table.setRowCount(0)
        for record in self.visible_records:
            row = self.file_table.rowCount()
            self.file_table.insertRow(row)
            try:
                stat = record.path.stat()
                size = format_file_size(stat.st_size)
                modified = datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M"
                )
            except OSError:
                size = "不可用"
                modified = ""
            values = [
                "★" if record.recommended else "",
                record.use_case,
                record.description,
                record.path.name,
                self._relative_path(record.path),
                size,
                modified,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(PATH_ROLE, str(record.path))
                if column == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self.file_table.setItem(row, column, item)
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
        for item in self.file_table.selectedItems():
            path = Path(str(item.data(PATH_ROLE) or ""))
            if path and path not in paths:
                paths.append(path)
        return paths

    def _selected_record(self) -> ResultRecord | None:
        paths = self._selected_paths()
        if not paths:
            return None
        target = paths[0]
        return next((record for record in self.records if record.path == target), None)

    def _update_preview(self) -> None:
        record = self._selected_record()
        self.preview_movie = None
        if record is None:
            self.preview.show_text("选择一个文件查看说明或预览。")
            self.preview_stack.setCurrentWidget(self.preview)
            return
        path = record.path
        suffix = path.suffix.casefold()
        if suffix == ".gif":
            self.preview_movie = self.preview.show_gif(path)
            self.preview_stack.setCurrentWidget(self.preview)
            return
        if suffix in {".png", ".jpg", ".jpeg"}:
            self.preview.show_image(path)
            self.preview_stack.setCurrentWidget(self.preview)
            return
        if suffix in {".csv", ".xlsx"}:
            try:
                row_limit = preview_row_limit(path)
                if suffix == ".csv":
                    headers, rows = read_csv_preview(path, row_limit)
                else:
                    headers, rows = read_xlsx_preview(path, row_limit)
                self._show_data_preview(headers, rows)
                self.data_preview.setToolTip(
                    f"轻量预览：已加载全部 {len(headers)} 列；"
                    f"当前最多读取前 {row_limit} 行"
                )
                self.preview_stack.setCurrentWidget(self.data_preview)
            except (OSError, UnicodeError, csv.Error, zipfile.BadZipFile, KeyError) as error:
                self.text_preview.setPlainText(
                    self._text_preview(record) + f"\n\n表格预览失败：{error}"
                )
                self.preview_stack.setCurrentWidget(self.text_preview)
            return
        self.text_preview.setPlainText(self._text_preview(record))
        self.preview_stack.setCurrentWidget(self.text_preview)

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
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "尚未选择", "请先选择一个结果文件。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths[0])))

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
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "尚未选择", "请先选择一个或多个结果文件。")
            return
        destination = QFileDialog.getExistingDirectory(
            self, "选择导出目录", str(self.current_scope)
        )
        if not destination:
            return
        destination_root = Path(destination)
        copied = 0
        for source in paths:
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
