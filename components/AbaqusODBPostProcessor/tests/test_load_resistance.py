from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import pytest

from abaqus_odb_postprocessor.postprocess_core import (
    build_load_resistance_table,
    build_xlsx,
    plot_load_resistance,
    read_csv,
)


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_history_output(output_dir: Path) -> None:
    write_rows(
        output_dir / "data" / "load_point_raw.csv",
        [
            {
                "SequenceIndex": 0,
                "StepIndex": 2,
                "StepName": "Load-U",
                "FrameIndex": 0,
                "IncrementNumber": 0,
                "StepTime": 0.0,
                "TotalTime": 2.0,
                "U3_mm": 0.0,
                "RF3_N": 0.0,
            },
            {
                "SequenceIndex": 1,
                "StepIndex": 2,
                "StepName": "Load-U",
                "FrameIndex": 1,
                "IncrementNumber": 1,
                "StepTime": 1.0,
                "TotalTime": 3.0,
                "U3_mm": 10.0,
                "RF3_N": 1_000_000.0,
            },
        ],
    )
    write_rows(
        output_dir / "History_Output" / "contact_history_raw.csv",
        [
            {
                "SequenceIndex": 0,
                "StepIndex": 2,
                "StepName": "Load-U",
                "FrameIndex": 0,
                "IncrementNumber": 0,
                "StepTime": 0.0,
                "TotalTime": 2.0,
                "KEY_1_CFN3_N": 0.0,
                "KEY_2_CFN3_N": 0.0,
                "KEY_3_CFN3_N": 0.0,
                "KEY_4_CFN3_N": 0.0,
                "PILE_CFS3_N": 0.0,
                "CUSTOM KEY OUTPUT": 0.0,
            },
            {
                "SequenceIndex": 1,
                "StepIndex": 2,
                "StepName": "Load-U",
                "FrameIndex": 1,
                "IncrementNumber": 1,
                "StepTime": 1.0,
                "TotalTime": 3.0,
                "KEY_1_CFN3_N": -100_000.0,
                "KEY_2_CFN3_N": -100_000.0,
                "KEY_3_CFN3_N": -100_000.0,
                "KEY_4_CFN3_N": -100_000.0,
                "PILE_CFS3_N": -500_000.0,
                "CUSTOM KEY OUTPUT": 12.5,
            },
        ],
    )
    (output_dir / "metadata.json").write_text(
        json.dumps({"odb_path": "sample.odb", "load_direction": "3"}),
        encoding="utf-8",
    )


def test_load_resistance_table_converts_history_to_engineering_units(
    tmp_path: Path,
) -> None:
    make_history_output(tmp_path)
    target = build_load_resistance_table(tmp_path)
    assert target == tmp_path / "History_Output" / "load_resistance_processed.csv"
    rows = read_csv(target)
    final = rows[-1]

    assert float(final["PileTopDisplacement_mm"]) == pytest.approx(10.0)
    assert float(final["PileTopReaction_kN"]) == pytest.approx(1000.0)
    assert float(final["KEY_1_HistorySigned_kN"]) == pytest.approx(-100.0)
    assert float(final["KEY_1_Bearing_kN"]) == pytest.approx(100.0)
    assert float(final["RootKeyHistorySignedSum_kN"]) == pytest.approx(-400.0)
    assert float(final["RootKeyTotalBearing_kN"]) == pytest.approx(400.0)
    assert float(final["RootKeyAverageBearing_kN"]) == pytest.approx(100.0)
    assert float(final["PileShaftFriction_kN"]) == pytest.approx(500.0)
    assert float(final["PileShaftFrictionSigned_kN"]) == pytest.approx(-500.0)
    assert float(final["UnresolvedResistance_kN"]) == pytest.approx(100.0)
    assert float(final["RootKeyShare_percent"]) == pytest.approx(40.0)
    assert float(final["PileShaftShare_percent"]) == pytest.approx(50.0)
    assert float(final["UnresolvedShare_percent"]) == pytest.approx(10.0)
    assert final["ContactHistoryStatus"] == "aligned"
    assert float(final["CUSTOM KEY OUTPUT"]) == pytest.approx(12.5)
    notes = json.loads(
        (tmp_path / "History_Output" / "load_resistance_notes.json").read_text(
            encoding="utf-8"
        )
    )
    assert notes["root_key_count"] == 4
    assert notes["unprocessed_history_columns"] == ["CUSTOM KEY OUTPUT"]
    assert "不能未经核查直接等同为桩端阻力" in notes["warning"]


def test_load_resistance_result_is_plotted_and_added_to_summary(
    tmp_path: Path,
) -> None:
    make_history_output(tmp_path)
    build_load_resistance_table(tmp_path)
    plot = plot_load_resistance(tmp_path)
    assert plot is not None and plot.is_file()

    workbook = build_xlsx(tmp_path)
    with zipfile.ZipFile(workbook) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
    assert 'name="Load_Sharing"' in workbook_xml
    assert 'name="Contact_Raw"' in workbook_xml


def test_multilevel_root_key_ids_remain_separate_and_are_grouped(
    tmp_path: Path,
) -> None:
    make_history_output(tmp_path)
    write_rows(
        tmp_path / "History_Output" / "contact_history_raw.csv",
        [
            {
                "SequenceIndex": 0,
                "KEY_1_1_CFN3_N": 0.0,
                "KEY_1_2_CFN3_N": 0.0,
                "KEY_2_1_CFN3_N": 0.0,
                "KEY_2_2_CFN3_N": 0.0,
                "PILE_CFS3_N": 0.0,
            },
            {
                "SequenceIndex": 1,
                "KEY_1_1_CFN3_N": -50_000.0,
                "KEY_1_2_CFN3_N": -150_000.0,
                "KEY_2_1_CFN3_N": -200_000.0,
                "KEY_2_2_CFN3_N": -100_000.0,
                "PILE_CFS3_N": -300_000.0,
            },
        ],
    )

    target = build_load_resistance_table(tmp_path)
    final = read_csv(target)[-1]
    assert float(final["KEY_1_1_Bearing_kN"]) == pytest.approx(50.0)
    assert float(final["KEY_1_2_Bearing_kN"]) == pytest.approx(150.0)
    assert float(final["KEY_2_1_Bearing_kN"]) == pytest.approx(200.0)
    assert float(final["KEY_2_2_Bearing_kN"]) == pytest.approx(100.0)
    assert float(final["KEY_1_GroupTotalBearing_kN"]) == pytest.approx(200.0)
    assert float(final["KEY_1_GroupAverageBearing_kN"]) == pytest.approx(100.0)
    assert float(final["KEY_2_GroupTotalBearing_kN"]) == pytest.approx(300.0)
    assert float(final["RootKeyTotalBearing_kN"]) == pytest.approx(500.0)
    assert int(float(final["RootKeyCount"])) == 4

    notes = json.loads(
        (tmp_path / "History_Output" / "load_resistance_notes.json").read_text(
            encoding="utf-8"
        )
    )
    assert notes["root_key_ids"] == [
        "KEY_1_1",
        "KEY_1_2",
        "KEY_2_1",
        "KEY_2_2",
    ]
    assert notes["root_key_groups"] == ["KEY_1", "KEY_2"]
