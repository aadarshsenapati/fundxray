"""Invariants every adapter must satisfy, whatever the AMC layout."""
from hypothesis import given, settings as hsettings
from hypothesis import strategies as st

from fundxray_core.identifiers.isin import is_valid, normalise
from fundxray_core.identifiers.names import normalise_name


@given(st.text(min_size=0, max_size=60))
@hsettings(max_examples=200)
def test_name_normalisation_never_crashes(s):
    assert isinstance(normalise_name(s), str)


@given(st.text(alphabet=st.characters(whitelist_categories=("Lu", "Nd")), min_size=0, max_size=20))
@hsettings(max_examples=300)
def test_isin_validation_never_crashes_and_is_deterministic(s):
    assert is_valid(s) == is_valid(s)


@given(st.text(min_size=1, max_size=30))
def test_normalise_is_idempotent(s):
    once = normalise(s)
    assert normalise(once) == once
