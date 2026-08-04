from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDoubleValidator, QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)


MIME_ODB_PATHS = "application/x-abaqus-odb-paths"
FIELD_NAMES = (
    "PILE_U_MAG",
    "PILE_CON_S_MISES",
    "PILE_STEEL_S_MISES",
    "PILE_CON_DAMAGET",
    "PILE_CON_DAMAGEC",
    "SOIL_PEEQ_XZ",
    "SOIL_PEMAG_XZ",
    "SOIL_S33_XZ",
    "SOIL_S_MISES_XZ",
    "REBAR_LONG_S_MISES_UNDEFORMED",
    "REBAR_LONG_S11_UNDEFORMED",
)


class ComparisonTree(QTreeWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDragEnabled(True)
        self.setAcceptDrops(False)
        self.setDropIndicatorShown(False)
        self.setDefaultDropAction(Qt.CopyAction)

    @staticmethod
    def kind(item: QTreeWidgetItem | None) -> str:
        return "" if item is None else str(item.data(0, Qt.UserRole) or "")

    @staticmethod
    def odb_path(item: QTreeWidgetItem | None) -> str:
        return "" if item is None else str(item.data(0, Qt.UserRole + 1) or "")

    @staticmethod
    def group_id(item: QTreeWidgetItem | None) -> str:
        return "" if item is None else str(item.data(0, Qt.UserRole + 2) or "")

    def selected_odb_paths(self) -> list[str]:
        paths = []
        for item in self.selectedItems():
            if self.kind(item) == "odb":
                path = self.odb_path(item)
                if path and path not in paths:
                    paths.append(path)
        return paths

    def startDrag(self, supported_actions: Qt.DropActions) -> None:
        paths = self.selected_odb_paths()
        if not paths:
            return
        mime = QMimeData()
        mime.setData(MIME_ODB_PATHS, json.dumps(paths, ensure_ascii=False).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


def odb_paths_from_mime(mime_data) -> list[str]:
    if not mime_data.hasFormat(MIME_ODB_PATHS):
        return []
    try:
        values = json.loads(bytes(mime_data.data(MIME_ODB_PATHS)).decode("utf-8"))
    except Exception:
        return []
    return [str(value) for value in values if str(value)]


class GroupDropTable(QTableWidget):
    odbPathsDropped = Signal(list)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_ODB_PATHS):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_ODB_PATHS):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = odb_paths_from_mime(event.mimeData())
        if not paths:
            event.ignore()
            return
        self.odbPathsDropped.emit(paths)
        event.acceptProposedAction()


class LegendRangeDialog(QDialog):
    def __init__(self, group_name: str, overrides: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"图例范围：{group_name}")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("自动：按本组全部 ODB 的已选帧汇总；手动：锁定该变量的本组上下限。"))
        self.table = QTableWidget(len(FIELD_NAMES), 4)
        self.table.setHorizontalHeaderLabels(["变量", "模式", "最小值", "最大值"])
        self.table.setColumnWidth(0, 270)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(3, 160)
        self.rows: list[tuple[str, QComboBox, QLineEdit, QLineEdit]] = []
        validator = QDoubleValidator(-1.0e300, 1.0e300, 16, self)
        validator.setNotation(QDoubleValidator.ScientificNotation)
        for row, field_name in enumerate(FIELD_NAMES):
            self.table.setItem(row, 0, QTableWidgetItem(field_name))
            mode = QComboBox()
            mode.addItems(["自动", "手动"])
            minimum = QLineEdit()
            maximum = QLineEdit()
            minimum.setValidator(validator)
            maximum.setValidator(validator)
            rule = overrides.get(field_name, {})
            manual = rule.get("mode") == "manual"
            mode.setCurrentText("手动" if manual else "自动")
            if manual:
                minimum.setText(str(rule.get("min", "")))
                maximum.setText(str(rule.get("max", "")))
            minimum.setEnabled(manual)
            maximum.setEnabled(manual)
            mode.currentTextChanged.connect(
                lambda text, lo=minimum, hi=maximum: (
                    lo.setEnabled(text == "手动"), hi.setEnabled(text == "手动")
                )
            )
            self.table.setCellWidget(row, 1, mode)
            self.table.setCellWidget(row, 2, minimum)
            self.table.setCellWidget(row, 3, maximum)
            self.rows.append((field_name, mode, minimum, maximum))
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._values: dict[str, dict[str, Any]] = {}

    def _validate(self) -> None:
        values: dict[str, dict[str, Any]] = {}
        for field_name, mode, minimum, maximum in self.rows:
            if mode.currentText() != "手动":
                continue
            try:
                lower = float(minimum.text())
                upper = float(maximum.text())
            except ValueError:
                QMessageBox.warning(self, "图例范围无效", f"{field_name} 的上下限必须是数字。")
                return
            if upper <= lower:
                QMessageBox.warning(self, "图例范围无效", f"{field_name} 的最大值必须大于最小值。")
                return
            values[field_name] = {"mode": "manual", "min": lower, "max": upper}
        self._values = values
        self.accept()

    def values(self) -> dict[str, dict[str, Any]]:
        return self._values
