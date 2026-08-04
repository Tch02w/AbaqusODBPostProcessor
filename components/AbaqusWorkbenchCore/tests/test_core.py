from __future__ import annotations

from abaqus_workbench_core.paths import resolve_application_data_dir
from abaqus_workbench_core.processes import is_environment_startup_noise


def test_windows_local_and_roaming_paths_are_explicit() -> None:
    environ = {
        "LOCALAPPDATA": r"C:\Local",
        "APPDATA": r"C:\Roaming",
    }
    assert str(
        resolve_application_data_dir(
            "Tool",
            windows_scope="local",
            environ=environ,
            platform="win32",
        )
    ) == r"C:\Local\Tool"
    assert str(
        resolve_application_data_dir(
            "Tool",
            windows_scope="roaming",
            environ=environ,
            platform="win32",
        )
    ) == r"C:\Roaming\Tool"


def test_environment_startup_noise_is_shared() -> None:
    assert is_environment_startup_noise("** Visual Studio 2026 Developer Command Prompt")
    assert not is_environment_startup_noise("Abaqus JOB job-1 COMPLETED")
