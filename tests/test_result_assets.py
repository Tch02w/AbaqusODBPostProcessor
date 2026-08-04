from __future__ import annotations

from pathlib import Path

from abaqus_odb_postprocessor.result_assets import (
    numeric_asset_dir,
    numeric_asset_is_valid,
    resolve_group_member_asset,
    write_group_member_manifest,
    write_numeric_asset_manifest,
)


def test_group_member_resolves_shared_numeric_asset(tmp_path: Path) -> None:
    asset = numeric_asset_dir(tmp_path, tmp_path / "A.odb", "a" * 64, "b" * 64)
    (asset / "data").mkdir(parents=True)
    (asset / "data" / "timeline_alignment.csv").write_text(
        "SequenceIndex\n0\n", encoding="utf-8"
    )
    (asset / "rebar").mkdir()
    (asset / "freebody").mkdir()
    (asset / "metadata.json").write_text("{}", encoding="utf-8")
    (asset / "summary.xlsx").write_bytes(b"xlsx")
    write_numeric_asset_manifest(
        asset,
        odb_path=tmp_path / "A.odb",
        content_fingerprint="a" * 64,
        numeric_config_hash="b" * 64,
        abaqus_version="2025",
    )
    assert numeric_asset_is_valid(
        asset,
        content_fingerprint="a" * 64,
        numeric_config_hash="b" * 64,
        abaqus_version="2025",
    )

    group_output = tmp_path / "组A" / "batch" / "A"
    group_output.mkdir(parents=True)
    write_group_member_manifest(
        group_output,
        asset_dir=asset,
        comparison_group="组A",
        odb_path=tmp_path / "A.odb",
        content_fingerprint="a" * 64,
        numeric_config_hash="b" * 64,
    )
    assert resolve_group_member_asset(group_output) == asset.resolve()
