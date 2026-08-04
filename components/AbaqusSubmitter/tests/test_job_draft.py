import json
import tempfile
import unittest
from pathlib import Path

from abaqus_submitter.app_settings import (
    load_app_settings,
    load_settings_section,
    save_app_settings,
    save_settings_section,
)
from abaqus_submitter.command import (
    build_abaqus_command,
    queue_item_to_options,
)
from abaqus_submitter.job_draft import LocalJobDraft
from abaqus_submitter.models import QueueItem
from abaqus_submitter.scheduler_adapter import queue_item_to_job_specification


class LocalJobDraftTests(unittest.TestCase):
    def test_visible_submission_values_build_the_exact_local_command(self) -> None:
        draft = LocalJobDraft(
            inp_file=r"C:\Models\phase2.inp",
            job_name="custom_phase2",
            cpus=0,
            memory_value="80",
            memory_unit="%",
            oldjob_path=r"C:\Models\phase1.odb",
            fortran_path=r"C:\Models\umat.for",
            interactive=True,
            datacheck=True,
            abaqus_command=r"C:\Program Files\SIMULIA\abaqus.bat",
        )

        command = build_abaqus_command(draft.to_submit_options())

        self.assertEqual(
            command,
            '"C:\\Program Files\\SIMULIA\\abaqus.bat" '
            "job=custom_phase2 input=phase2.inp oldjob=phase1 "
            'user="C:\\Models\\umat.for" memory=80% datacheck interactive',
        )

    def test_draft_values_reach_queue_and_scheduler(self) -> None:
        draft = LocalJobDraft(
            inp_file=r"D:\Jobs\phase2.inp",
            job_name="final_phase",
            cpus=12,
            memory_value="24",
            memory_unit="GB",
            abaqus_command="abq2024.bat",
            priority=10,
            calculation_root_dir=r"D:\Abaqus_Cal",
            archive_dir=r"E:\Results",
        )
        item = QueueItem(inp_path=draft.inp_file)

        draft.apply_to_queue_item(item)
        specification = queue_item_to_job_specification(
            item,
            queue_items=[item],
            submitted_order=0,
        )
        restored_options = queue_item_to_options(item, default_cpus=4)

        self.assertEqual(item.job_name, "final_phase")
        self.assertEqual(item.memory, "24gb")
        self.assertEqual(item.abaqus_command, "abq2024.bat")
        self.assertEqual(specification.resources.cores, 12)
        self.assertEqual(specification.priority, 10)
        self.assertEqual(restored_options.abaqus_command, "abq2024.bat")
        self.assertTrue(item.archive_after_complete)
        self.assertTrue(item.cleanup_after_archive)

    def test_local_workspace_paths_are_validated_before_submission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            valid_draft = LocalJobDraft(
                calculation_root_dir=temporary_dir,
                archive_dir=temporary_dir,
            )
            invalid_draft = LocalJobDraft(
                calculation_root_dir=str(Path(temporary_dir) / "missing"),
            )

            self.assertEqual(valid_draft.validate_local_paths(), (True, ""))
            ok, message = invalid_draft.validate_local_paths()
            self.assertFalse(ok)
            self.assertIn("SSD 工作目录不存在", message)


class AppSettingsTests(unittest.TestCase):
    def test_section_save_preserves_legacy_and_unrelated_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "settings.json"
            save_app_settings(
                {
                    "qt_ssd_work_dir": r"D:\OldCal",
                    "unrelated": {"keep": True},
                },
                path=path,
            )

            save_settings_section(
                "workbench",
                {
                    "job_name": "phase2",
                    "calculation_root_dir": r"D:\NewCal",
                },
                path=path,
            )

            payload = load_app_settings(path)
            self.assertEqual(payload["qt_ssd_work_dir"], r"D:\OldCal")
            self.assertEqual(payload["unrelated"], {"keep": True})
            self.assertEqual(
                load_settings_section("workbench", path=path),
                {
                    "job_name": "phase2",
                    "calculation_root_dir": r"D:\NewCal",
                },
            )
            with path.open("r", encoding="utf-8") as stream:
                self.assertEqual(json.load(stream), payload)


if __name__ == "__main__":
    unittest.main()
