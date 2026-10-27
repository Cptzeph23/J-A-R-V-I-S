import pytest
from hypothesis import given
from hypothesis import strategies as st

from apps.finance.domain import KindError, normalize_text, resolve_kind


@given(st.text())
def test_normalize_text_is_idempotent(value):
    once = normalize_text(value)
    assert normalize_text(once) == once


def test_normalize_text_collapses_case_and_whitespace():
    assert normalize_text("  NAIVAS \t Supermarket\n") == "naivas supermarket"


@pytest.mark.parametrize(
    ("hint", "amount", "expected"),
    [
        (None, 500, "income"),
        ("", -500, "expense"),
        ("income", 500, "income"),
        ("EXPENSE", -500, "expense"),
        ("fee", -50, "fee"),
        ("refund", 70, "refund"),
        ("transfer", 100, "transfer_in"),
        ("transfer", -100, "transfer_out"),
    ],
)
def test_resolve_kind_valid(hint, amount, expected):
    assert resolve_kind(hint, amount) == expected


@pytest.mark.parametrize(
    ("hint", "amount", "code"),
    [
        ("income", -1, "sign_mismatch"),
        ("refund", -1, "sign_mismatch"),
        ("expense", 1, "sign_mismatch"),
        ("fee", 1, "sign_mismatch"),
        ("bribe", 1, "invalid_type"),
        (None, 0, "zero_amount"),
        ("transfer", 0, "zero_amount"),
    ],
)
def test_resolve_kind_rejects_contradictions(hint, amount, code):
    with pytest.raises(KindError) as info:
        resolve_kind(hint, amount)
    assert info.value.code == code


@given(st.integers(min_value=-(10**12), max_value=10**12).filter(lambda n: n != 0))
def test_unhinted_kind_always_agrees_with_the_sign(amount):
    kind = resolve_kind(None, amount)
    assert (kind == "income") == (amount > 0)
