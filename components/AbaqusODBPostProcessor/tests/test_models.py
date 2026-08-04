from abaqus_odb_postprocessor.models import choose_name, normalized_name


def test_set_name_normalization_handles_hyphens():
    assert normalized_name("SET-PILE_CON") == "SETPILECON"
    assert choose_name(["SET-PILE_CON"], "SETPILECON") == "SET-PILE_CON"

