import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from abaqus_submitter.cluster_ui import ClusterTopologyWidget, SubmissionWizardDialog
from abaqus_submitter.models import QueueItem
from abaqus_submitter.qt_compat import QtCore, QtWidgets
from abaqus_submitter.queue_manager import QueueManagerDialog
from abaqus_submitter.remote_frontend import (
    ExecutionLocation,
    OdbMergeDraft,
    RemoteFrontendBridge,
    RemoteJobDraft,
    ServerProfileDraft,
)
from abaqus_submitter.ui_styles import build_main_stylesheet
from abaqus_submitter.ui_components import (
    ResourceProgressBar,
    SegmentedSpinBox,
    WorkbenchComboBox,
    configure_popup_menu,
)
from abaqus_submitter.workbench_ui import (
    JobConfigurationWorkbench,
    LocalResourceSnapshot,
    ProjectRemoteExplorer,
    ResourceSummaryWidget,
    WorkbenchLogDock,
    WorkbenchPropertiesPanel,
)


class RemoteFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_merge_preview_preserves_both_raw_odb_files(self) -> None:
        merge = OdbMergeDraft(
            original_job="xxx1",
            current_job="xxx2",
            auto_merge=False,
            include_history=True,
            compress_result=False,
            server_side=True,
            retain_originals=True,
            result_name_source="current",
            custom_result_name="",
        )

        self.assertEqual(
            merge.preview(),
            "xxx1_original.odb + xxx2_original.odb → xxx2_joined.odb",
        )

    def test_remote_job_payload_keeps_typed_frontend_choices(self) -> None:
        server = ServerProfileDraft(
            profile_name="compute-01",
            host="10.10.2.31",
            username="abaqus_user",
            authentication="SSH 私钥",
            host_fingerprint="SHA256:test",
            abaqus_command="abaqus.bat",
            compute_root=r"D:\Abaqus_Cal",
            allowed_roots=(r"D:\Abaqus_Cal", r"E:\Projects"),
        )
        merge = OdbMergeDraft(
            original_job="phase1",
            current_job="phase2",
            auto_merge=True,
            include_history=True,
            compress_result=False,
            server_side=True,
            retain_originals=True,
            result_name_source="custom",
            custom_result_name="final",
        )

        payload = RemoteJobDraft(
            job_name="phase2",
            inp_path=r"E:\Projects\phase2.inp",
            location=ExecutionLocation.SERVER_EXISTING,
            server=server,
            remote_path=r"E:\Projects\phase2.inp",
            cpus=16,
            memory="32gb",
            merge=merge,
        ).as_payload()

        self.assertEqual(payload["location"], "server_existing")
        self.assertEqual(payload["merge"]["result_stem"], "final_joined")
        self.assertEqual(
            payload["merge"]["preview"],
            "phase1_original.odb + phase2_original.odb → final_joined.odb",
        )

    def test_wizard_emits_remote_request_without_network_side_effects(self) -> None:
        bridge = RemoteFrontendBridge()
        wizard = SubmissionWizardDialog(bridge)
        requests: list[dict] = []
        bridge.submitRemoteJobRequested.connect(requests.append)
        wizard.location_existing.setChecked(True)
        wizard.remote_path_edit.setText(r"E:\Projects\Gearbox\xxx2.inp")

        wizard.submit_current()

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["location"], "server_existing")
        self.assertEqual(requests[0]["job_name"], "xxx2")
        wizard.close()

    def test_resource_view_queue_count_updates_real_summary(self) -> None:
        topology = ClusterTopologyWidget()

        topology.set_queue_count(7)

        self.assertEqual(topology.queue_summary_label.text(), "队列作业：7")
        topology.close()

    def test_resource_view_only_adds_nodes_from_real_snapshots(self) -> None:
        topology = ClusterTopologyWidget()

        self.assertEqual(topology.resource_table.rowCount(), 1)
        self.assertEqual(topology.resource_table.item(0, 0).text(), "本机")
        visible_text = "\n".join(
            label.text() for label in topology.findChildren(QtWidgets.QLabel)
        )
        self.assertNotIn("Scheduler Core", visible_text)
        self.assertNotIn("SFTP 暂存区", visible_text)
        self.assertNotIn("ODB 合并器", visible_text)
        self.assertNotIn("远程节点 1", visible_text)
        self.assertNotIn("远程节点 2", visible_text)

        topology.apply_remote_resource_snapshot(
            {
                "profile_name": "compute-01",
                "connected": True,
                "cpu_used": 8,
                "cpu_total": 32,
                "memory_used_gb": 24,
                "memory_total_gb": 128,
                "compute_root": r"D:\Abaqus_Cal",
                "active_jobs": ("gearbox",),
            }
        )

        self.assertEqual(topology.resource_table.rowCount(), 2)
        self.assertEqual(topology.resource_table.item(1, 0).text(), "compute-01")
        self.assertEqual(topology.resource_table.item(1, 1).text(), "在线")
        topology.close()

    def test_left_resource_cards_share_one_real_snapshot(self) -> None:
        snapshot = LocalResourceSnapshot(
            logical_cpus=32,
            cpu_percent=25.0,
            memory_used_bytes=8 * 1024**3,
            memory_total_bytes=32 * 1024**3,
            memory_percent=25.0,
        )
        queue_item = QueueItem(
            inp_path=r"C:\Models\bracket.inp",
            job_name="bracket",
            status="等待运行",
        )
        explorer = ProjectRemoteExplorer()
        properties = WorkbenchPropertiesPanel()

        explorer.refresh(
            [queue_item],
            queue_item.inp_path,
            resource_snapshot=snapshot,
        )
        properties.refresh([queue_item], queue_item, snapshot)

        self.assertEqual(
            explorer.tree.selectionMode(),
            QtWidgets.QAbstractItemView.SelectionMode.NoSelection,
        )
        self.assertIn("8 / 32 线程（25%）", explorer.resource_summary.cpu_label.text())
        self.assertFalse(hasattr(properties, "resource_group"))
        self.assertFalse(hasattr(properties, "resource_rows"))

        self.assertEqual(explorer.tree.topLevelItemCount(), 1)
        self.assertEqual(explorer.tree.topLevelItem(0).text(0), "Models")
        self.assertEqual(
            explorer.resource_summary.resource_selector.currentData(),
            "local",
        )
        self.assertTrue(explorer.resource_summary.resource_selector.isHidden())
        self.assertEqual(
            set(explorer.resource_summary.resource_choice_buttons),
            {"local"},
        )

        remote_snapshot = {
            "profile_name": "compute-01",
            "connected": True,
            "cpu_used": 8,
            "cpu_total": 32,
            "cpu_percent": 25,
            "memory_used_gb": 24,
            "memory_total_gb": 128,
            "running_jobs": 2,
            "waiting_jobs": 1,
        }
        explorer.apply_remote_snapshot(remote_snapshot)
        explorer.resource_summary.select_resource("compute-01")

        self.assertEqual(
            explorer.resource_summary.resource_selector.currentData(),
            "compute-01",
        )
        self.assertTrue(explorer.resource_summary.resource_selector.isHidden())
        self.assertEqual(
            set(explorer.resource_summary.resource_choice_buttons),
            {"local", "compute-01"},
        )
        remote_card_lines = explorer.resource_summary.resource_choice_buttons[
            "compute-01"
        ].text().splitlines()
        self.assertEqual(len(remote_card_lines), 4)
        self.assertTrue(remote_card_lines[1].startswith("CPU"))
        self.assertTrue(remote_card_lines[2].startswith("内存"))
        self.assertTrue(remote_card_lines[3].startswith("作业"))
        self.assertEqual(
            explorer.resource_summary.status_label.text(),
            "● compute-01 · 已连接",
        )
        self.assertIn(
            "8 / 32 线程（25%）",
            explorer.resource_summary.cpu_label.text(),
        )
        remote_card = explorer.resource_summary.resource_choice_buttons[
            "compute-01"
        ]
        self.assertEqual(
            remote_card.resource_rows["CPU"][1].text(),
            "8 / 32",
        )
        self.assertEqual(
            remote_card.resource_rows["内存"][1].text(),
            "24.0 / 128.0 GB",
        )
        self.assertEqual(
            remote_card.resource_rows["作业"][1].text(),
            "2 / 3",
        )
        explorer.resize(300, 720)
        explorer.show()
        self.app.processEvents()
        progress_widths = {
            bar.width()
            for card in explorer.resource_summary.resource_choice_buttons.values()
            for bar, _detail in card.resource_rows.values()
        }
        self.assertEqual(len(progress_widths), 1)
        for card in explorer.resource_summary.resource_choice_buttons.values():
            for bar, detail in card.resource_rows.values():
                self.assertEqual(detail.geometry().left(), bar.geometry().left())
                self.assertEqual(detail.geometry().right(), bar.geometry().right())
                self.assertLessEqual(
                    card.contentsRect().right() - bar.geometry().right(),
                    4,
                )
                self.assertTrue(
                    detail.alignment()
                    & QtCore.Qt.AlignmentFlag.AlignRight
                )

        explorer.refresh(
            [queue_item],
            queue_item.inp_path,
            resource_snapshot=snapshot,
        )
        properties.refresh([queue_item], queue_item, snapshot)
        self.assertEqual(
            explorer.resource_summary.status_label.text(),
            "● compute-01 · 已连接",
        )
        self.assertEqual(
            remote_card.resource_rows["CPU"][0].value(),
            25,
        )
        explorer.close()
        properties.close()

    def test_resource_summary_uses_dropdown_only_after_three_nodes(self) -> None:
        explorer = ProjectRemoteExplorer()
        explorer.setStyleSheet(build_main_stylesheet())
        try:
            for index in range(1, 3):
                explorer.apply_remote_snapshot(
                    {
                        "profile_name": f"server-{index}",
                        "connected": True,
                        "cpu_used": index,
                        "cpu_total": 16,
                        "memory_used_gb": 8,
                        "memory_total_gb": 64,
                    }
                )
            summary = explorer.resource_summary
            self.assertEqual(summary.resource_selector.count(), 3)
            self.assertTrue(summary.resource_selector.isHidden())
            self.assertFalse(summary.resource_choices_widget.isHidden())

            explorer.apply_remote_snapshot(
                {
                    "profile_name": "server-3",
                    "connected": True,
                    "cpu_used": 3,
                    "cpu_total": 16,
                    "memory_used_gb": 12,
                    "memory_total_gb": 64,
                }
            )
            self.assertEqual(summary.resource_selector.count(), 4)
            self.assertFalse(summary.resource_selector.isHidden())
            self.assertTrue(summary.resource_choices_widget.isHidden())
            self.assertGreaterEqual(summary.resource_selector.width(), 170)
            self.assertEqual(summary.resource_selector.height(), 24)
        finally:
            explorer.close()

    def test_narrow_resource_summary_keeps_values_inside_each_card(self) -> None:
        summary = ResourceSummaryWidget()
        summary.setStyleSheet(build_main_stylesheet())
        summary.refresh(
            [
                QueueItem(
                    inp_path=r"C:\Models\a.inp",
                    job_name="a",
                    status="运行中",
                ),
                QueueItem(
                    inp_path=r"C:\Models\b.inp",
                    job_name="b",
                    status="等待运行",
                ),
            ],
            scheduler_ready=True,
            resource_snapshot=LocalResourceSnapshot(
                logical_cpus=3,
                cpu_percent=19,
                memory_used_bytes=int(24.4 * 1024**3),
                memory_total_bytes=int(63.7 * 1024**3),
                memory_percent=38.3,
            ),
        )
        summary.setFixedWidth(248)
        summary.show()
        self.app.processEvents()

        try:
            card = summary.resource_choice_buttons["local"]
            card_right = card.contentsRect().right()
            widths = set()
            for bar, detail in card.resource_rows.values():
                widths.add(bar.width())
                self.assertLessEqual(detail.geometry().right(), card_right)
            self.assertEqual(len(widths), 1)
        finally:
            summary.close()

    def test_low_resource_usage_keeps_a_rounded_visible_progress_marker(
        self,
    ) -> None:
        bar = ResourceProgressBar()
        bar.setRange(0, 100)
        bar.setFixedWidth(132)
        bar.show()

        for value in range(1, 5):
            bar.setValue(value)
            self.app.processEvents()
            image = bar.grab().toImage()
            blue_points = []
            for y in range(image.height()):
                for x in range(image.width()):
                    color = image.pixelColor(x, y)
                    if (
                        color.blue() > 150
                        and color.red() < 80
                        and color.green() < 140
                    ):
                        blue_points.append((x, y))
            self.assertTrue(blue_points)
            visible_width = (
                max(x for x, _y in blue_points)
                - min(x for x, _y in blue_points)
                + 1
            )
            self.assertGreaterEqual(visible_width, bar.height())
            self.assertNotEqual(
                image.pixelColor(0, 0),
                image.pixelColor(0, bar.height() // 2),
            )
        bar.close()

    def test_segmented_spinbox_preserves_native_spinbox_behavior(self) -> None:
        spinbox = SegmentedSpinBox()
        spinbox.setRange(0, 2)
        spinbox.setValue(1)

        self.assertIsInstance(spinbox, QtWidgets.QSpinBox)
        self.assertEqual(
            spinbox.buttonSymbols(),
            QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons,
        )
        self.assertTrue(spinbox.property("segmentedSpin"))
        self.assertIsNotNone(spinbox.step_down_button)
        self.assertIsNotNone(spinbox.step_up_button)

        spinbox.step_up_button.click()
        self.assertEqual(spinbox.value(), 2)
        self.assertFalse(spinbox.step_up_button.isEnabled())
        spinbox.step_down_button.click()
        self.assertEqual(spinbox.value(), 1)
        spinbox.close()

    def test_segmented_spinbox_buttons_preserve_all_four_outer_corners(self) -> None:
        spinbox = SegmentedSpinBox()
        spinbox.setStyleSheet(build_main_stylesheet())
        spinbox.setRange(0, 10)
        spinbox.setValue(4)
        spinbox.resize(180, 32)
        spinbox.show()

        image = spinbox.grab().toImage()
        corners_and_interiors = (
            ((1, 1), (5, 5)),
            ((image.width() - 2, 1), (image.width() - 6, 5)),
            ((1, image.height() - 2), (5, image.height() - 6)),
            (
                (image.width() - 2, image.height() - 2),
                (image.width() - 6, image.height() - 6),
            ),
        )
        for corner, interior in corners_and_interiors:
            self.assertNotEqual(
                image.pixelColor(*corner),
                image.pixelColor(*interior),
            )
        spinbox.close()

    def test_primary_workbench_contains_real_job_and_odb_inputs(self) -> None:
        bridge = RemoteFrontendBridge()
        wizard = SubmissionWizardDialog(bridge)
        workbench = JobConfigurationWorkbench(wizard, bridge)

        workbench.input_path_edit.setText(r"C:\Models\phase2.inp")
        workbench.job_name_edit.setText("custom_phase2")
        workbench.set_oldjob_path(r"C:\Models\phase1.odb")
        workbench.set_fortran_path(r"C:\Models\umat.for")
        workbench.cpu_spin.setValue(0)
        workbench.memory_value_edit.setText("80")
        workbench.memory_unit_combo.setCurrentText("%")
        workbench.abaqus_command_edit.setText("abq2024.bat")
        workbench.priority_combo.setCurrentIndex(
            workbench.priority_combo.findData(10)
        )
        workbench.set_merge_original_path(r"C:\Models\phase1.odb")
        workbench.set_merge_restart_path(r"C:\Models\phase2.odb")
        workbench.copy_original_check.setChecked(True)
        self.assertEqual(workbench.copy_original_check.text(), "保留原始 ODB")

        draft = workbench.local_job_draft()
        combos = (
            *wizard.findChildren(QtWidgets.QComboBox),
            *workbench.findChildren(QtWidgets.QComboBox),
        )
        spinboxes = (
            *wizard.findChildren(QtWidgets.QSpinBox),
            *workbench.findChildren(QtWidgets.QSpinBox),
        )
        self.assertTrue(combos)
        self.assertTrue(spinboxes)
        self.assertTrue(
            all(isinstance(combo, WorkbenchComboBox) for combo in combos)
        )
        self.assertTrue(
            all(isinstance(spinbox, SegmentedSpinBox) for spinbox in spinboxes)
        )
        for combo in combos:
            self.assertEqual(
                combo.view().objectName(),
                "workbenchComboPopup",
            )
        for form in (
            *wizard.findChildren(QtWidgets.QFormLayout),
            *workbench.findChildren(QtWidgets.QFormLayout),
        ):
            self.assertTrue(
                form.labelAlignment()
                & QtCore.Qt.AlignmentFlag.AlignVCenter
            )
        self.assertEqual(draft.job_name, "custom_phase2")
        self.assertEqual(draft.cpus, 0)
        self.assertEqual((draft.memory_value, draft.memory_unit), ("80", "%"))
        self.assertEqual(draft.abaqus_command, "abq2024.bat")
        self.assertEqual(draft.priority, 10)
        for index in (1, 2):
            self.assertFalse(workbench.execution_combo.model().item(index).isEnabled())
        self.assertEqual(workbench.oldjob_path_edit.text(), r"C:\Models\phase1.odb")
        self.assertEqual(workbench.fortran_path_edit.text(), r"C:\Models\umat.for")
        self.assertEqual(
            workbench.merge_output_edit.text(),
            r"C:\Models\phase2_joined.odb",
        )
        workbench.merge_name_source_combo.setCurrentIndex(
            workbench.merge_name_source_combo.findData("original")
        )
        self.assertEqual(
            workbench.merge_output_edit.text(),
            r"C:\Models\phase1_joined.odb",
        )
        self.assertTrue(workbench.merge_execute_btn.isEnabled())
        self.assertTrue(workbench.merge_values()["copy_original"])
        self.assertTrue(workbench.export_settings()["merge_copy_original"])
        self.assertEqual(
            workbench.choose_merge_output_btn.parentWidget().objectName(),
            "formFieldRow",
        )
        visible_text = "\n".join(
            label.text() for label in workbench.findChildren(QtWidgets.QLabel)
        )
        self.assertNotIn("多级合并", visible_text)
        self.assertNotIn("验证清单", visible_text)
        self.assertNotIn("复制\n", visible_text)
        self.assertEqual(wizard.oldjob_row.text(), r"C:\Models\phase1.odb")
        self.assertEqual(wizard.for_row.text(), r"C:\Models\umat.for")
        workbench.close()
        wizard.close()

    def test_single_job_ssd_option_controls_the_real_work_directory(self) -> None:
        bridge = RemoteFrontendBridge()
        wizard = SubmissionWizardDialog(bridge)
        workbench = JobConfigurationWorkbench(wizard, bridge)

        workbench.input_path_edit.setText(r"C:\Models\phase2.inp")
        workbench.calculation_root_edit.setText(r"D:\Abaqus_Cal")
        workbench.archive_root_edit.setText(r"G:\Abaqus_Arc")

        self.assertEqual(workbench.use_ssd_check.text(), "使用 SSD 目录计算")
        self.assertFalse(workbench.use_ssd_check.isChecked())
        self.assertFalse(workbench.calculation_root_edit.isEnabled())
        self.assertFalse(workbench.choose_calculation_root_btn.isEnabled())
        self.assertFalse(workbench.archive_root_edit.isEnabled())
        self.assertFalse(workbench.choose_archive_root_btn.isEnabled())
        self.assertEqual(workbench.local_job_draft().calculation_root_dir, "")
        self.assertEqual(workbench.local_job_draft().archive_dir, "")
        self.assertIn("C:\\Models", workbench.path_decision_label.text())

        workbench.use_ssd_check.setChecked(True)
        draft = workbench.local_job_draft()
        self.assertTrue(workbench.calculation_root_edit.isEnabled())
        self.assertTrue(workbench.choose_calculation_root_btn.isEnabled())
        self.assertTrue(workbench.archive_root_edit.isEnabled())
        self.assertTrue(workbench.choose_archive_root_btn.isEnabled())
        self.assertTrue(draft.use_ssd_calculation)
        self.assertEqual(draft.calculation_root_dir, r"D:\Abaqus_Cal")
        self.assertEqual(draft.archive_dir, r"G:\Abaqus_Arc")
        self.assertIn("本机 SSD 计算", workbench.path_decision_label.text())

        workbench.use_ssd_check.setChecked(False)
        saved = workbench.export_settings()
        self.assertFalse(saved["use_ssd_calculation"])
        self.assertEqual(saved["calculation_root_dir"], r"D:\Abaqus_Cal")
        self.assertEqual(saved["archive_dir"], r"G:\Abaqus_Arc")

        workbench.apply_settings(
            {
                "use_ssd_calculation": True,
                "calculation_root_dir": r"E:\SSD_Work",
                "archive_dir": r"F:\Results",
            }
        )
        self.assertTrue(workbench.use_ssd_check.isChecked())
        self.assertTrue(workbench.calculation_root_edit.isEnabled())
        self.assertEqual(
            workbench.local_job_draft().calculation_root_dir,
            r"E:\SSD_Work",
        )

        workbench.calculation_root_edit.clear()
        ok, message = workbench.local_job_draft().validate_local_paths()
        self.assertFalse(ok)
        self.assertIn("请选择 SSD 工作目录", message)

        workbench.close()
        wizard.close()

    def test_job_configuration_fits_the_center_without_horizontal_clipping(
        self,
    ) -> None:
        bridge = RemoteFrontendBridge()
        wizard = SubmissionWizardDialog(bridge)
        workbench = JobConfigurationWorkbench(wizard, bridge)
        workbench.setStyleSheet(build_main_stylesheet())
        workbench.resize(750, 690)
        workbench.show()
        self.app.processEvents()

        scroll = workbench.findChild(QtWidgets.QScrollArea)
        self.assertIsNotNone(scroll)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(
            scroll.widget().minimumSizeHint().width(),
            scroll.viewport().width(),
        )
        workbench.close()
        wizard.close()

    def test_cpu_and_memory_share_one_resource_row(self) -> None:
        bridge = RemoteFrontendBridge()
        wizard = SubmissionWizardDialog(bridge)
        workbench = JobConfigurationWorkbench(wizard, bridge)
        workbench.setStyleSheet(build_main_stylesheet())
        workbench.resize(750, 690)
        workbench.show()
        self.app.processEvents()

        cpu_center = workbench.cpu_spin.mapTo(
            workbench,
            workbench.cpu_spin.rect().center(),
        )
        memory_center = workbench.memory_value_edit.mapTo(
            workbench,
            workbench.memory_value_edit.rect().center(),
        )
        priority_center = workbench.priority_combo.mapTo(
            workbench,
            workbench.priority_combo.rect().center(),
        )
        self.assertLessEqual(abs(cpu_center.y() - memory_center.y()), 1)
        self.assertLessEqual(abs(cpu_center.y() - priority_center.y()), 1)
        resource_label = workbench.server_form.labelForField(
            workbench.resource_row
        )
        self.assertEqual(resource_label.text(), "CPU")
        self.assertEqual(
            workbench.cpu_spin.mapTo(workbench, QtCore.QPoint()).x(),
            workbench.abaqus_command_edit.mapTo(
                workbench,
                QtCore.QPoint(),
            ).x(),
        )
        self.assertEqual(workbench.cpu_spin.width(), 180)
        cpu_right = workbench.cpu_spin.mapTo(
            workbench,
            workbench.cpu_spin.rect().topRight(),
        ).x()
        memory_left = workbench.memory_label.mapTo(
            workbench,
            workbench.memory_label.rect().topLeft(),
        ).x()
        self.assertGreaterEqual(memory_left - cpu_right, 18)
        self.assertLess(
            workbench.memory_unit_combo.width(),
            workbench.memory_value_edit.width(),
        )
        self.assertEqual(workbench.priority_combo.width(), 170)
        priority_right = workbench.priority_combo.mapTo(
            workbench,
            workbench.priority_combo.rect().topRight(),
        ).x()
        command_right = workbench.abaqus_command_edit.mapTo(
            workbench,
            workbench.abaqus_command_edit.rect().topRight(),
        ).x()
        self.assertEqual(priority_right, command_right)
        for group in (
            workbench.cpu_resource_group,
            workbench.memory_resource_group,
            workbench.priority_resource_group,
        ):
            self.assertEqual(group.objectName(), "resourceInlineGroup")
        scroll = workbench.findChild(QtWidgets.QScrollArea)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        workbench.close()
        wizard.close()

    def test_closed_combo_boxes_ignore_mouse_wheel_changes(self) -> None:
        class IgnoredWheelEvent:
            def __init__(self) -> None:
                self.ignored = False

            def ignore(self) -> None:
                self.ignored = True

        bridge = RemoteFrontendBridge()
        wizard = SubmissionWizardDialog(bridge)
        workbench = JobConfigurationWorkbench(wizard, bridge)
        combos = (
            *wizard.findChildren(QtWidgets.QComboBox),
            *workbench.findChildren(QtWidgets.QComboBox),
        )

        for combo in combos:
            if combo.count() < 2:
                continue
            combo.setCurrentIndex(0)
            event = IgnoredWheelEvent()
            combo.wheelEvent(event)
            self.assertTrue(event.ignored)
            self.assertEqual(combo.currentIndex(), 0)
        workbench.close()
        wizard.close()

    def test_job_configuration_uses_one_full_width_card_per_row(self) -> None:
        bridge = RemoteFrontendBridge()
        wizard = SubmissionWizardDialog(bridge)
        workbench = JobConfigurationWorkbench(wizard, bridge)
        workbench.resize(750, 690)
        workbench.show()
        self.app.processEvents()

        groups = {
            group.title(): group
            for group in workbench.findChildren(QtWidgets.QGroupBox)
        }
        input_group = groups["作业输入"]
        resource_group = groups["执行环境与资源"]
        self.assertEqual(input_group.x(), resource_group.x())
        self.assertEqual(input_group.width(), resource_group.width())
        self.assertGreater(resource_group.y(), input_group.geometry().bottom())
        self.assertFalse(workbench.original_job_edit.isVisibleTo(workbench))
        workbench.close()
        wizard.close()

    def test_path_picker_buttons_use_one_compact_ellipsis_contract(self) -> None:
        class HostWindow(QtWidgets.QWidget):
            def reconcile_scheduler_state(self) -> None:
                pass

        bridge = RemoteFrontendBridge()
        wizard = SubmissionWizardDialog(bridge)
        workbench = JobConfigurationWorkbench(wizard, bridge)
        host = HostWindow()
        queue_manager = QueueManagerDialog(host, [], {}, embedded=True)

        try:
            path_picker_buttons = (
                wizard.inp_row.button,
                wizard.oldjob_row.button,
                wizard.for_row.button,
                wizard.browse_remote_btn,
                workbench.choose_input_btn,
                workbench.choose_original_btn,
                workbench.choose_fortran_btn,
                workbench.choose_calculation_root_btn,
                workbench.choose_archive_root_btn,
                workbench.choose_merge_original_btn,
                workbench.choose_merge_restart_btn,
                workbench.choose_merge_output_btn,
                queue_manager.choose_ssd_btn,
                queue_manager.choose_archive_btn,
                queue_manager.choose_work_dir_btn,
            )
            for button in path_picker_buttons:
                self.assertEqual(button.text(), "...")
                self.assertTrue(button.property("pathPicker"))
                self.assertTrue(button.toolTip())
                self.assertEqual(button.accessibleName(), button.toolTip())
                self.assertLessEqual(button.maximumWidth(), 32)
        finally:
            queue_manager.close()
            host.close()
            workbench.close()
            wizard.close()

    def test_push_button_focus_does_not_leave_a_selected_visual(self) -> None:
        host = QtWidgets.QWidget()
        host.setStyleSheet(build_main_stylesheet())
        layout = QtWidgets.QVBoxLayout(host)
        button = QtWidgets.QPushButton("取消选中")
        line_edit = QtWidgets.QLineEdit()
        layout.addWidget(button)
        layout.addWidget(line_edit)
        host.show()
        self.app.processEvents()

        try:
            line_edit.setFocus()
            self.app.processEvents()
            unfocused = button.grab().toImage()

            button.setFocus()
            self.app.processEvents()
            focused = button.grab().toImage()

            self.assertEqual(unfocused, focused)
        finally:
            host.close()

    def test_main_navigation_uses_real_task_tabs_and_shadowless_popups(self) -> None:
        from abaqus_submitter.main import MainWindow

        window = MainWindow()
        try:
            tab_names = [
                window.workbench_tabs.tabText(index)
                for index in range(window.workbench_tabs.count())
            ]
            self.assertEqual(
                tab_names[:3],
                ["新建作业", "作业队列", "作业概览"],
            )
            self.assertIn("新建作业", tab_names)
            self.assertIn("ODB 合并", tab_names)
            self.assertNotIn("作业配置", tab_names)
            self.assertNotIn("ODB 验证", tab_names)
            self.assertFalse(hasattr(window, "new_job_btn"))
            self.assertFalse(hasattr(window, "toolbar_submit_btn"))
            self.assertFalse(hasattr(window, "toolbar_stop_btn"))
            self.assertIsNone(
                window.findChild(QtWidgets.QFrame, "workbenchToolbar")
            )
            self.assertIs(
                window.workbench_menu_bar.parentWidget(),
                window.window_chrome,
            )
            self.assertIs(
                window.job_configuration.odb_merge_group.parentWidget(),
                window.odb_merge_content,
            )
            menus = window.findChildren(QtWidgets.QMenu)
            self.assertEqual(len(menus), 3)
            for menu in menus:
                self.assertEqual(menu.objectName(), "workbenchPopupMenu")
                self.assertTrue(
                    menu.windowFlags()
                    & QtCore.Qt.WindowType.NoDropShadowWindowHint
                )
                self.assertTrue(
                    menu.testAttribute(
                        QtCore.Qt.WidgetAttribute.WA_TranslucentBackground
                    )
                )
            stylesheet = build_main_stylesheet()
            self.assertIn("QMenu#workbenchPopupMenu", stylesheet)
            self.assertIn(
                "QAbstractItemView#workbenchComboPopup",
                stylesheet,
            )
            self.assertIn("border-radius: 6px", stylesheet)
        finally:
            window.close()

    def test_selected_resource_stays_in_the_left_resource_overview(self) -> None:
        from abaqus_submitter.main import MainWindow

        window = MainWindow()
        try:
            window.apply_remote_resource_snapshot(
                {
                    "profile_name": "server01",
                    "connected": True,
                    "cpu_used": 6,
                    "cpu_total": 24,
                    "cpu_percent": 25,
                    "memory_used_gb": 32,
                    "memory_total_gb": 128,
                    "running_jobs": 1,
                    "waiting_jobs": 2,
                }
            )
            summary = window.project_explorer.resource_summary
            self.assertTrue(summary.resource_selector.isHidden())
            summary.resource_choice_buttons["server01"].click()
            self.app.processEvents()

            card = summary.resource_choice_buttons["server01"]
            self.assertTrue(card.property("selected"))
            self.assertEqual(
                card.resource_rows["CPU"][1].text(),
                "6 / 24",
            )
            self.assertEqual(
                card.resource_rows["内存"][1].text(),
                "32.0 / 128.0 GB",
            )
            self.assertFalse(hasattr(window.properties_panel, "resource_group"))
            tree_labels = {
                window.project_explorer.tree.topLevelItem(index).text(0)
                for index in range(
                    window.project_explorer.tree.topLevelItemCount()
                )
            }
            self.assertNotIn("SSH 服务器", tree_labels)
        finally:
            window.close()

    def test_popup_menu_has_a_native_rounded_window_mask(self) -> None:
        host = QtWidgets.QWidget()
        host.resize(320, 240)
        host.show()
        menu = configure_popup_menu(QtWidgets.QMenu(host))
        menu.setStyleSheet(build_main_stylesheet())
        menu.addAction("新建作业")
        menu.addAction("退出")
        menu.popup(host.mapToGlobal(QtCore.QPoint(30, 30)))
        self.app.processEvents()

        try:
            mask = menu.mask()
            self.assertTrue(mask.isEmpty())
            self.assertTrue(
                menu.windowFlags()
                & QtCore.Qt.WindowType.FramelessWindowHint
            )
            image = menu.grab().toImage()
            corner_alphas = {
                image.pixelColor(x, y).alpha()
                for x in range(min(8, image.width()))
                for y in range(min(8, image.height()))
            }
            self.assertIn(0, corner_alphas)
            self.assertTrue(
                any(0 < alpha < 255 for alpha in corner_alphas)
            )
            border_color = image.pixelColor(image.width() // 2, 0)
            right_border_color = image.pixelColor(
                image.width() - 1,
                image.height() // 2,
            )
            bottom_border_color = image.pixelColor(
                image.width() // 2,
                image.height() - 1,
            )
            center_color = image.pixelColor(
                image.width() // 2,
                image.height() // 2,
            )
            self.assertEqual(
                border_color.name(),
                "#94a3b8",
            )
            self.assertEqual(right_border_color.name(), "#94a3b8")
            self.assertEqual(bottom_border_color.name(), "#94a3b8")
            self.assertNotEqual(border_color, center_color)
        finally:
            menu.close()
            host.close()

    def test_popup_surface_ignores_late_module_shutdown_events(self) -> None:
        menu = configure_popup_menu(QtWidgets.QMenu())
        surface = menu._rounded_popup_surface
        del surface.widget

        event = QtCore.QEvent(QtCore.QEvent.Type.Paint)
        self.assertFalse(surface.eventFilter(menu, event))
        menu.close()

    def test_combo_popup_mask_keeps_right_and_bottom_borders_visible(
        self,
    ) -> None:
        host = QtWidgets.QWidget()
        host.resize(320, 240)
        combo = WorkbenchComboBox(host)
        combo.setGeometry(20, 20, 180, 30)
        combo.addItems(["普通", "高", "低"])
        combo.setStyleSheet(build_main_stylesheet())
        host.show()
        combo.showPopup()
        self.app.processEvents()
        combo._position_popup_below()
        self.app.processEvents()

        try:
            popup = combo.view().window()
            mask = popup.mask()
            self.assertTrue(mask.isEmpty())
            self.assertTrue(
                popup.windowFlags()
                & QtCore.Qt.WindowType.FramelessWindowHint
            )
            image = popup.grab().toImage()
            corner_alphas = {
                image.pixelColor(x, y).alpha()
                for x in range(min(8, image.width()))
                for y in range(min(8, image.height()))
            }
            self.assertIn(0, corner_alphas)
            self.assertTrue(
                any(0 < alpha < 255 for alpha in corner_alphas)
            )
        finally:
            combo.hidePopup()
            host.close()

    def test_properties_panel_has_no_inert_close_glyph(self) -> None:
        panel = WorkbenchPropertiesPanel()
        try:
            labels = {
                label.text()
                for label in panel.findChildren(QtWidgets.QLabel)
            }
            self.assertNotIn("×", labels)
            self.assertNotIn("x", labels)
        finally:
            panel.close()

    def test_project_explorer_spans_full_height_and_log_aligns_with_center(
        self,
    ) -> None:
        from abaqus_submitter.main import MainWindow

        window = MainWindow()
        window.resize(1500, 920)
        window.show()
        self.app.processEvents()

        def window_rect(widget: QtWidgets.QWidget) -> QtCore.QRect:
            top_left = widget.mapTo(window, QtCore.QPoint())
            return QtCore.QRect(top_left, widget.size())

        try:
            explorer_rect = window_rect(window.project_explorer)
            tabs_rect = window_rect(window.workbench_tabs)
            properties_rect = window_rect(window.properties_panel)
            log_rect = window_rect(window.log_dock)

            self.assertEqual(
                window.workbench_outer_splitter.orientation(),
                QtCore.Qt.Orientation.Horizontal,
            )
            self.assertEqual(
                window.workbench_main_splitter.orientation(),
                QtCore.Qt.Orientation.Vertical,
            )
            self.assertEqual(explorer_rect.top(), tabs_rect.top())
            self.assertLessEqual(
                abs(explorer_rect.bottom() - log_rect.bottom()),
                1,
            )
            self.assertLessEqual(abs(log_rect.left() - tabs_rect.left()), 1)
            self.assertLessEqual(
                abs(log_rect.right() - properties_rect.right()),
                1,
            )
        finally:
            window.close()

    def test_queue_manager_can_be_embedded_as_a_primary_page(self) -> None:
        class HostWindow(QtWidgets.QWidget):
            def reconcile_scheduler_state(self) -> None:
                pass

        host = HostWindow()
        queue_manager = QueueManagerDialog(
            host,
            [],
            {
                "job_name": "custom_phase2",
                "cores": 8,
                "memory": "80%",
                "oldjob_path": "",
                "for_file": "",
                "interactive": False,
                "datacheck": False,
                "notify": True,
                "abaqus_command": "abq2024.bat",
                "priority": 10,
            },
            embedded=True,
        )
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(queue_manager, "作业队列")
        queue_actions: list[str] = []
        queue_manager.startQueueRequested.connect(
            lambda: queue_actions.append("start")
        )
        queue_manager.stopQueueRequested.connect(
            lambda: queue_actions.append("stop")
        )
        queue_manager.start_queue_btn.click()
        queue_manager.stop_queue_btn.click()
        queue_manager.current_inp = r"C:\Models\phase2.inp"
        self.assertTrue(
            queue_manager.add_candidate(
                queue_manager.current_inp,
                source="当前 INP",
            )
        )
        candidate = queue_manager.candidates[0]

        self.assertTrue(queue_manager.embedded)
        self.assertFalse(queue_manager.isWindow())
        self.assertEqual(tabs.tabText(0), "作业队列")
        self.assertEqual(queue_manager.confirm_btn.text(), "确认选中项加入队列")
        self.assertEqual(queue_manager.use_ssd_check.text(), "使用 SSD 目录计算")
        self.assertFalse(queue_manager.use_ssd_check.isChecked())
        self.assertFalse(queue_manager.ssd_dir_edit.isEnabled())
        self.assertFalse(queue_manager.archive_dir_edit.isEnabled())
        self.assertGreater(
            queue_manager.candidate_toolbar.indexOf(
                queue_manager.use_ssd_check
            ),
            queue_manager.candidate_toolbar.indexOf(queue_manager.confirm_btn),
        )
        self.assertEqual(queue_manager.start_queue_btn.text(), "开始队列")
        self.assertEqual(queue_manager.stop_queue_btn.text(), "终止队列")
        self.assertIn(
            "QDialog#embeddedQueueManager QPushButton",
            queue_manager.styleSheet(),
        )
        self.assertEqual(queue_actions, ["start", "stop"])
        self.assertEqual(queue_manager.hold_queue_btn.text(), "暂停调度")
        self.assertEqual(queue_manager.requeue_btn.text(), "重新排队")
        self.assertEqual(candidate.job_name, "custom_phase2")
        self.assertEqual(candidate.cores, 8)
        self.assertEqual(candidate.memory, "80%")
        self.assertEqual(candidate.abaqus_command, "abq2024.bat")
        self.assertEqual(candidate.priority, 10)
        tabs.close()
        host.close()

    def test_formal_queue_toolbar_uses_compact_unclipped_labels(self) -> None:
        class HostWindow(QtWidgets.QWidget):
            def reconcile_scheduler_state(self) -> None:
                pass

        host = HostWindow()
        queue_manager = QueueManagerDialog(host, [], {}, embedded=True)

        try:
            compact_actions = (
                (
                    queue_manager.remove_queue_btn,
                    "取消选中",
                    "取消选中的待运行作业",
                ),
                (
                    queue_manager.edit_queue_btn,
                    "编辑选中",
                    "编辑选中的待运行作业",
                ),
                (
                    queue_manager.terminate_queue_btn,
                    "终止选中",
                    "终止选中的运行中作业",
                ),
                (
                    queue_manager.clear_finished_btn,
                    "清理记录",
                    "清理已结束记录",
                ),
            )
            for button, text, tooltip in compact_actions:
                self.assertEqual(button.text(), text)
                self.assertEqual(button.toolTip(), tooltip)
                self.assertGreaterEqual(
                    button.sizeHint().width(),
                    button.fontMetrics().horizontalAdvance(text) + 16,
                )
            toolbar_buttons = (
                queue_manager.start_queue_btn,
                queue_manager.stop_queue_btn,
                queue_manager.remove_queue_btn,
                queue_manager.edit_queue_btn,
                queue_manager.hold_queue_btn,
                queue_manager.release_queue_btn,
                queue_manager.requeue_btn,
                queue_manager.terminate_queue_btn,
                queue_manager.clear_finished_btn,
            )
            required_width = (
                sum(button.sizeHint().width() for button in toolbar_buttons)
                + 6 * (len(toolbar_buttons) - 1)
                + 8
                + queue_manager.summary_label.sizeHint().width()
            )
            self.assertLessEqual(required_width, 960)
        finally:
            queue_manager.close()
            host.close()

    def test_candidate_ssd_option_controls_the_real_queue_work_directory(
        self,
    ) -> None:
        class HostWindow(QtWidgets.QWidget):
            def reconcile_scheduler_state(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ssd_root = root / "ssd"
            archive_root = root / "archive"
            ssd_root.mkdir()
            archive_root.mkdir()
            first_inp = root / "source_first.inp"
            second_inp = root / "source_second.inp"
            first_inp.write_text("*Heading\n", encoding="utf-8")
            second_inp.write_text("*Heading\n", encoding="utf-8")
            host = HostWindow()
            queue_items: list[QueueItem] = []
            queue_manager = QueueManagerDialog(
                host,
                queue_items,
                {
                    "job_name": "",
                    "cores": 8,
                    "memory": "80%",
                    "oldjob_path": "",
                    "for_file": "",
                    "interactive": False,
                    "datacheck": False,
                    "notify": True,
                    "abaqus_command": "abaqus.bat",
                    "priority": 0,
                },
                embedded=True,
            )
            queue_manager.ssd_dir_edit.setText(str(ssd_root))
            queue_manager.archive_dir_edit.setText(str(archive_root))

            queue_manager.use_ssd_check.setChecked(False)
            self.assertFalse(queue_manager.ssd_dir_edit.isEnabled())
            self.assertFalse(queue_manager.archive_dir_edit.isEnabled())
            self.assertTrue(queue_manager.add_candidate(str(first_inp)))
            queue_manager.candidates[0].selected = True
            with mock.patch.object(
                queue_manager,
                "confirm_selected_candidates_action",
                return_value=True,
            ):
                queue_manager.confirm_candidates()

            self.assertEqual(queue_items[0].calculation_root_dir, "")
            self.assertEqual(queue_items[0].effective_work_dir, str(root))
            self.assertEqual(queue_items[0].archive_dir, "")
            self.assertFalse(queue_items[0].archive_after_complete)

            queue_manager.use_ssd_check.setChecked(True)
            self.assertTrue(queue_manager.ssd_dir_edit.isEnabled())
            self.assertTrue(queue_manager.archive_dir_edit.isEnabled())
            self.assertTrue(queue_manager.add_candidate(str(second_inp)))
            queue_manager.candidates[0].selected = True
            with mock.patch.object(
                queue_manager,
                "confirm_selected_candidates_action",
                return_value=True,
            ):
                queue_manager.confirm_candidates()

            self.assertEqual(
                queue_items[1].calculation_root_dir,
                str(ssd_root),
            )
            self.assertEqual(
                queue_items[1].effective_work_dir,
                str(ssd_root / "source_second"),
            )
            self.assertEqual(queue_items[1].archive_dir, str(archive_root))
            self.assertTrue(queue_items[1].archive_after_complete)
            self.assertTrue(queue_items[1].cleanup_after_archive)
            queue_manager.close()
            host.close()

    def test_bottom_event_tables_use_the_workbench_header_style(self) -> None:
        history = QtWidgets.QPlainTextEdit()
        dock = WorkbenchLogDock(history)
        header = dock.transfer_table.horizontalHeader()

        self.assertEqual(header.objectName(), "dockTableHeader")
        self.assertEqual(header.height(), 28)
        self.assertFalse(header.highlightSections())
        self.assertFalse(header.sectionsClickable())
        self.assertFalse(dock.transfer_table.showGrid())
        stylesheet = build_main_stylesheet()
        self.assertIn("QLabel,\n            QCheckBox", stylesheet)
        self.assertIn("QWidget#formFieldRow", stylesheet)
        self.assertIn('QSpinBox[segmentedSpin="true"]', stylesheet)
        self.assertIn("QToolButton#spinStepDown", stylesheet)
        self.assertIn("QHeaderView#dockTableHeader::section", stylesheet)
        self.assertIn(
            "QAbstractItemView#workbenchComboPopup",
            stylesheet,
        )
        self.assertIn(
            "QFrame#runtimeInspector {\n"
            "                background: #ffffff;\n"
            "                border: 0;\n"
            "                border-radius: 0;",
            stylesheet,
        )
        dock.close()


if __name__ == "__main__":
    unittest.main()
