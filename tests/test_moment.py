from abaqus_odb_postprocessor.postprocess import interpolate_components


def test_moment_interpolation_is_linear_between_rebar_centroids():
    points = [(0.0, 10.0, 100.0, -50.0), (100.0, 30.0, 300.0, 50.0)]
    force, mx, my, rule = interpolate_components(points, 25.0, -10.0, 110.0)
    assert (force, mx, my) == (15.0, 150.0, -25.0)
    assert rule == "linear_between_element_centroids"


def test_moment_is_zero_outside_physical_rebar_extent():
    values = interpolate_components([(0.0, 10.0, 100.0, -50.0)], -20.0, -10.0, 110.0)
    assert values == (0.0, 0.0, 0.0, "outside_rebar_extent_zero")
