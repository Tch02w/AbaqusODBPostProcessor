import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from abaqus_submitter.app_paths import APP_DATA_DIR_ENV, APP_NAME, resolve_app_data_dir


class AppPathsTests(unittest.TestCase):
    def test_explicit_data_directory_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {APP_DATA_DIR_ENV: temp_dir}, clear=False):
                self.assertEqual(resolve_app_data_dir(), Path(temp_dir).resolve())

    def test_windows_default_uses_local_app_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            environment = {APP_DATA_DIR_ENV: "", "LOCALAPPDATA": temp_dir}
            with patch.dict(os.environ, environment, clear=False):
                self.assertEqual(resolve_app_data_dir(), Path(temp_dir) / APP_NAME)


if __name__ == "__main__":
    unittest.main()
