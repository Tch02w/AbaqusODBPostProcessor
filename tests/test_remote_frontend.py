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
)
from abaqus_submitter.workbench_ui import (
    JobConfigurationWorkbench,
    LocalResourceSnapshot,
    ProjectRemoteExplorer,
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

    def test_topology_queue_count_updates_scheduler_hub(self) -> None:
        topology = ClusterTopologyWidget()

        topology.set_queue_count(7)

        self.assertEqual(topology.hub_queue.text(), "队列 7")
        topology.close()

    def test_sftp_transfer_status_is_not_drawn_inside_thin_progress_bar(self) -> None:
        topology = ClusterTopologyWidget()

        self.assertEqual(topology.transfer_status_label.text(), "当前无传输任务")
        self.assertGreaterEqual(
            topology.transfer_status_label.sizeHint().height(),
            topology.transfer_status_label.fontMetrics().height(),
        )
        self.assertFalse(topology.transfer_bar.isTextVisible())
        topology.close()

    def test_workbench_resource_panels_share_one_real_snapshot(self) -> None:
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
        cpu_detail = properties.resource_rows["CPU"][1].text()
        self.assertEqual(cpu_detail, "25% · 32 线程")
        server_root = explorer.tree.topLevelItem(
            explorer.tree.topLevelItemCount() - 1
        )
        self.assertEqual(server_root.child(0).text(0), "○ 尚未连接远程服务器")
        explorer.close()
        properties.close()

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
            self.assertTrue(
                combo.view().windowFlags()
                & QtCore.Qt.WindowType.NoDropShadowWindowHint
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

    def test_job_configuration_fits_the_center_without_horizontal_clipping(
        self,
    ) -> None:
        bridge = RemoteFrontendBridge()
        wizard = SubmissionWizardDialog(bridge)
        workbench = JobConfigurationWorkbench(wizard, bridge)
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
