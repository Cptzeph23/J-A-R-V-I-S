from __future__ import annotations

import pytest

from apps.common import secretbox
from apps.common.secretbox import SecretBoxError, generate_key, open_sealed, reseal, seal


def test_round_trip_and_format():
    token = seal("refresh-token-value", "oauth.refresh:1")
    assert token.startswith("v1.")
    assert "refresh-token-value" not in token
    assert open_sealed(token, "oauth.refresh:1") == "refresh-token-value"


def test_same_plaintext_never_seals_the_same_way():
    assert seal("x", "c") != seal("x", "c")


def test_a_value_moved_to_another_context_does_not_open():
    token = seal("secret", "oauth.refresh:owner-a")
    with pytest.raises(SecretBoxError):
        open_sealed(token, "oauth.refresh:owner-b")


def _flip_inside_the_ciphertext(token: str) -> str:
    # A character well inside the payload, always changed (a fixed replacement would sometimes
    # equal the original and leave the value intact).
    index = len(token) - 8
    return token[:index] + ("A" if token[index] != "A" else "B") + token[index + 1 :]


@pytest.mark.parametrize(
    "mutate", [_flip_inside_the_ciphertext, lambda t: "v2" + t[2:], lambda t: "junk"]
)
def test_tampered_or_unknown_values_are_refused(mutate):
    with pytest.raises(SecretBoxError):
        open_sealed(mutate(seal("secret", "c")), "c")


def test_errors_never_contain_the_secret():
    token = seal("hunter2-secret", "c")
    with pytest.raises(SecretBoxError) as info:
        open_sealed(token, "other")
    assert "hunter2" not in str(info.value)


def test_rotation_adds_a_key_and_reseals(settings):
    old, new = generate_key(), generate_key()
    settings.FIELD_ENCRYPTION_KEYS = f"k1:{old}"
    token = seal("secret", "c")
    assert secretbox.key_id_of(token) == "k1"
    settings.FIELD_ENCRYPTION_KEYS = f"k2:{new},k1:{old}"
    assert open_sealed(token, "c") == "secret"  # old keys still open
    moved = reseal(token, "c")
    assert secretbox.key_id_of(moved) == "k2"
    assert reseal(moved, "c") == moved
    settings.FIELD_ENCRYPTION_KEYS = f"k2:{new}"
    assert open_sealed(moved, "c") == "secret"
    with pytest.raises(SecretBoxError):
        open_sealed(token, "c")  # k1 removed


@pytest.mark.parametrize(
    "spec", ["nocolon", "k1:not base64!!", "k1:AAAA", f"k1:{generate_key()},k1:{generate_key()}"]
)
def test_bad_key_configuration_is_rejected(settings, spec):
    settings.FIELD_ENCRYPTION_KEYS = spec
    with pytest.raises(SecretBoxError):
        seal("x", "c")
