import pytest

from abaqus_odb_postprocessor.legends import (
    aggregate_group_ranges,
    choose_sequences,
    parse_sequence_expression,
)


def catalog(indices, ranges=None, prefracture=None):
    return {
        "frame_catalog": [
            {"SequenceIndex": value, "ranges": (ranges or {}).get(value, {})}
            for value in indices
        ],
        "auto_detection": {"prefracture_sequence_index": prefracture},
    }


def test_manual_sequence_expression_supports_ranges():
    assert parse_sequence_expression("0, 2, 4-6", range(8)) == [0, 2, 4, 5, 6]


def test_manual_sequence_expression_rejects_missing_frame():
    with pytest.raises(ValueError):
        parse_sequence_expression("0,9", range(4))


def test_auto_selects_prefracture_and_last():
    assert choose_sequences(catalog([0, 1, 2, 3], prefracture=2), "auto") == [2, 3]


def test_group_range_uses_only_selected_frames_and_isolated_groups():
    jobs = [
        {"comparison_group": "A", "selected_sequence_indices": [0],
         "range_scan": catalog([0, 1], {0: {"PILE_U_MAG": {"min": 1, "max": 3}},
                                        1: {"PILE_U_MAG": {"min": -99, "max": 99}}})},
        {"comparison_group": "A", "selected_sequence_indices": [2],
         "range_scan": catalog([2], {2: {"PILE_U_MAG": {"min": -2, "max": 8}}})},
        {"comparison_group": "B", "selected_sequence_indices": [0],
         "range_scan": catalog([0], {0: {"PILE_U_MAG": {"min": 10, "max": 20}}})},
    ]
    plans = aggregate_group_ranges(jobs)
    assert plans["A"]["PILE_U_MAG"]["min"] == -2
    assert plans["A"]["PILE_U_MAG"]["max"] == 8
    assert plans["B"]["PILE_U_MAG"]["min"] == 10


def test_damage_palette_limits_are_fixed_but_observed_values_are_kept():
    jobs = [{"comparison_group": "A", "selected_sequence_indices": [0],
             "range_scan": catalog([0], {0: {"PILE_CON_DAMAGET": {"min": 0.12, "max": 0.7}}})}]
    limits = aggregate_group_ranges(jobs)["A"]["PILE_CON_DAMAGET"]
    assert limits["min"] == 0.0
    assert limits["max"] == 0.886
    assert limits["observed_min"] == 0.12
    assert limits["observed_max"] == 0.7
