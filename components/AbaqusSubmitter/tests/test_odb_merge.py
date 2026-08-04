import tempfile
import unittest
from pathlib import Path
from unittest import mock

from abaqus_submitter.odb_merge import (
    MergeConflictPolicy,
    OdbMergeError,
    OdbMergeRequest,
    _OdbMergeWorker,
    build_merge_plan,
    normalize_joined_output,
)


class OdbMergePlanTests(unittest.TestCase):
    def test_output_name_always_uses_joined_suffix(self) -> None:
        self.assertEqual(
            normalize_joined_output(Path("custom.odb")),
            Path("custom_joined.odb"),
        )
        self.assertEqual(
            normalize_joined_output(Path("phase2_joined.odb")),
            Path("phase2_joined.odb"),
        )

    def test_plan_uses_copies_and_omits_copyoriginal_argument(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "xxx1.odb"
            restart = root / "xxx2.odb"
            original.write_bytes(b"original")
            restart.write_bytes(b"restart")

            plan = build_merge_plan(
                OdbMergeRequest(
                    original_odb=original,
                    restart_odb=restart,
                    output_odb=root / "custom.odb",
                    abaqus_command="abaqus.bat",
                    include_history=True,
                    compress_result=False,
                )
            )

            self.assertEqual(plan.request.output_odb, root / "custom_joined.odb")
            self.assertEqual(plan.original_backup, root / "xxx1_original.odb")
            self.assertEqual(plan.restart_backup, root / "xxx2_original.odb")
            self.assertIn("history", plan.restartjoin_arguments)
            self.assertNotIn("compressresult", plan.restartjoin_arguments)
            self.assertFalse(
                any("copyoriginal" in argument for argument in plan.restartjoin_arguments)
            )
            self.assertNotEqual(plan.working_odb, original)
            self.assertNotEqual(plan.working_odb, plan.staged_original_backup)
            self.assertEqual(plan.restartjoin_output_odb, plan.working_odb)

    def test_copyoriginal_targets_the_abaqus_generated_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "xxx1.odb"
            restart = root / "xxx2.odb"
            original.write_bytes(b"original")
            restart.write_bytes(b"restart")

            plan = build_merge_plan(
                OdbMergeRequest(
                    original_odb=original,
                    restart_odb=restart,
                    output_odb=root / "custom.odb",
                    copy_original=True,
                )
            )

            self.assertIn("copyoriginal", plan.restartjoin_arguments)
            self.assertEqual(
                plan.restartjoin_output_odb,
                plan.staging_dir / f"Restart_{plan.working_odb.name}",
            )
            self.assertEqual(
                plan.validation_arguments[-1],
                plan.restartjoin_output_odb.name,
            )

    def test_auto_number_never_reuses_existing_result_or_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "xxx1.odb"
            restart = root / "xxx2.odb"
            output = root / "xxx2_joined.odb"
            original.write_bytes(b"original")
            restart.write_bytes(b"restart")
            output.write_bytes(b"existing result")
            (root / "xxx1_original.odb").write_bytes(b"existing backup")

            plan = build_merge_plan(
                OdbMergeRequest(
                    original_odb=original,
                    restart_odb=restart,
                    output_odb=output,
                    conflict_policy=MergeConflictPolicy.AUTO_NUMBER,
                )
            )

            self.assertEqual(plan.request.output_odb, root / "xxx2_joined_002.odb")
            self.assertEqual(plan.original_backup, root / "xxx1_original_002.odb")
            self.assertEqual(plan.restart_backup, root / "xxx2_original.odb")

    def test_output_cannot_replace_a_source_odb(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "xxx1_joined.odb"
            restart = root / "xxx2.odb"
            original.write_bytes(b"original")
            restart.write_bytes(b"restart")

            with self.assertRaisesRegex(OdbMergeError, "不能覆盖"):
                build_merge_plan(
                    OdbMergeRequest(
                        original_odb=original,
                        restart_odb=restart,
                        output_odb=original,
                    )
                )

    def test_worker_publishes_joined_file_without_modifying_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "xxx1.odb"
            restart = root / "xxx2.odb"
            output = root / "xxx2_joined.odb"
            original.write_bytes(b"original source")
            restart.write_bytes(b"restart source")
            worker = _OdbMergeWorker(
                OdbMergeRequest(
                    original_odb=original,
                    restart_odb=restart,
                    output_odb=output,
                )
            )
            results = []
            failures = []
            worker.succeeded.connect(results.append)
            worker.failed.connect(failures.append)

            def fake_process(_worker, plan, arguments):
                if arguments[0] == "restartjoin":
                    plan.working_odb.write_bytes(
                        plan.working_odb.read_bytes() + b" + joined"
                    )
                    return "restartjoin complete"
                return "ABASUB_ODB_VALIDATION_OK"

            with mock.patch.object(
                _OdbMergeWorker,
                "_run_process",
                autospec=True,
                side_effect=fake_process,
            ):
                worker.run()

            self.assertEqual(failures, [])
            self.assertEqual(len(results), 1)
            self.assertEqual(original.read_bytes(), b"original source")
            self.assertEqual(restart.read_bytes(), b"restart source")
            self.assertEqual(
                (root / "xxx1_original.odb").read_bytes(),
                b"original source",
            )
            self.assertEqual(
                (root / "xxx2_original.odb").read_bytes(),
                b"restart source",
            )
            self.assertEqual(output.read_bytes(), b"original source + joined")
            self.assertEqual(list(root.glob(".abasub-odb-merge-*")), [])

    def test_worker_publishes_copyoriginal_result_without_modifying_sources(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "xxx1.odb"
            restart = root / "xxx2.odb"
            output = root / "xxx2_joined.odb"
            original.write_bytes(b"original source")
            restart.write_bytes(b"restart source")
            worker = _OdbMergeWorker(
                OdbMergeRequest(
                    original_odb=original,
                    restart_odb=restart,
                    output_odb=output,
                    copy_original=True,
                )
            )
            results = []
            failures = []
            worker.succeeded.connect(results.append)
            worker.failed.connect(failures.append)

            def fake_process(_worker, plan, arguments):
                if arguments[0] == "restartjoin":
                    self.assertIn("copyoriginal", arguments)
                    plan.restartjoin_output_odb.write_bytes(
                        plan.working_odb.read_bytes() + b" + joined"
                    )
                    return "restartjoin complete"
                return "ABASUB_ODB_VALIDATION_OK"

            with mock.patch.object(
                _OdbMergeWorker,
                "_run_process",
                autospec=True,
                side_effect=fake_process,
            ):
                worker.run()

            self.assertEqual(failures, [])
            self.assertEqual(len(results), 1)
            self.assertEqual(original.read_bytes(), b"original source")
            self.assertEqual(restart.read_bytes(), b"restart source")
            self.assertEqual(output.read_bytes(), b"original source + joined")
            self.assertEqual(list(root.glob(".abasub-odb-merge-*")), [])


if __name__ == "__main__":
    unittest.main()
