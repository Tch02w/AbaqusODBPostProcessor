from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "abaqus_odb_postprocessor"
    / "abaqus_scripts"
    / "odb_compatibility.py"
)


class FakeOdb:
    steps = {"Step-1": object()}

    def close(self) -> None:
        pass


def load_upgrade_script(monkeypatch, open_odb):
    odb_access = types.ModuleType("odbAccess")
    odb_access.isUpgradeRequiredForOdb = lambda **_kwargs: False
    odb_access.openOdb = open_odb

    def upgrade_odb(**kwargs) -> None:
        Path(kwargs["upgradedOdbPath"]).write_bytes(b"new odb")

    odb_access.upgradeOdb = upgrade_odb
    monkeypatch.setitem(sys.modules, "odbAccess", odb_access)
    return runpy.run_path(str(SCRIPT_PATH))


def test_upgrade_keeps_original_name_and_backs_up_old_odb(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "model.odb"
    backup = tmp_path / "model-old.odb"
    temporary = tmp_path / "model-upgrading-2025.odb"
    source.write_bytes(b"old odb")
    module = load_upgrade_script(
        monkeypatch,
        lambda **_kwargs: FakeOdb(),
    )

    result = module["upgrade_one"](
        str(source),
        str(source),
        str(backup),
        str(temporary),
    )

    assert result["status"] == "upgraded"
    assert result["upgraded_path"] == str(source)
    assert result["backup_path"] == str(backup)
    assert source.read_bytes() == b"new odb"
    assert backup.read_bytes() == b"old odb"
    assert not temporary.exists()


def test_upgrade_restores_original_when_final_validation_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "model.odb"
    backup = tmp_path / "model-old.odb"
    temporary = tmp_path / "model-upgrading-2025.odb"
    source.write_bytes(b"old odb")

    def open_odb(**kwargs):
        path = Path(kwargs["path"])
        if path == source and path.read_bytes() == b"new odb":
            raise RuntimeError("simulated validation failure")
        return FakeOdb()

    module = load_upgrade_script(monkeypatch, open_odb)
    result = module["upgrade_one"](
        str(source),
        str(source),
        str(backup),
        str(temporary),
    )

    assert result["status"] == "upgrade_failed"
    assert "原 ODB 已保持或恢复" in result["message"]
    assert source.read_bytes() == b"old odb"
    assert not backup.exists()
    assert not temporary.exists()
