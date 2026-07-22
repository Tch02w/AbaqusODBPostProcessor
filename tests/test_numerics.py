from abaqus_odb_postprocessor.numerics import interpolate_rebar_force


def test_outside_physical_bar_extent_is_zero():
    value, rule = interpolate_rebar_force([(25.0, 10.0), (75.0, 30.0)], 10.0, 20.0, 80.0)
    assert value == 0.0
    assert rule == "outside_rebar_extent_zero"


def test_between_bar_end_and_end_centroid_uses_end_element():
    value, rule = interpolate_rebar_force([(25.0, 10.0), (75.0, 30.0)], 22.0, 20.0, 80.0)
    assert value == 10.0
    assert rule == "end_element_constant"


def test_linear_interpolation_between_element_centroids():
    value, rule = interpolate_rebar_force([(25.0, 10.0), (75.0, 30.0)], 50.0, 20.0, 80.0)
    assert value == 20.0
    assert rule == "linear_between_element_centroids"

