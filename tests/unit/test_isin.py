from fundxray_core.identifiers.isin import is_equity, is_valid, normalise
from fundxray_core.identifiers.names import blocking_key, normalise_name


def test_valid_indian_isins():
    for isin in ["INE040A01034", "INE002A01018", "INE009A01021", "INE090A01021"]:
        assert is_valid(isin), isin
        assert is_equity(isin)


def test_check_digit_catches_corruption():
    assert not is_valid("INE040A01035")
    assert not is_valid("INE040A01033")


def test_non_equity_and_garbage():
    assert is_valid("US0378331005")
    assert not is_equity("US0378331005")
    assert not is_valid("GARBAGE")
    assert not is_valid(None)


def test_normalise_strips_noise():
    assert normalise(" ine040a01034 ") == "INE040A01034"
    assert normalise("INE-040-A01034") == "INE040A01034"


def test_name_normalisation_collapses_variants():
    variants = ["HDFC Bank Ltd", "HDFC Bank Limited", "HDFC BANK LTD.", "hdfc bank ltd"]
    assert len({normalise_name(v) for v in variants}) == 1


def test_blocking_key_groups_candidates():
    assert blocking_key("HDFC Bank Ltd") == blocking_key("HDFC Bank Limited")
