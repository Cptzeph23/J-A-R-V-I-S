import uuid

import pytest

from apps.phonelog.services.hashing import counterpart_hash, normalize_address

OWNER = uuid.UUID(int=1)
OTHER = uuid.UUID(int=2)


@pytest.mark.parametrize(
    "raw",
    [
        "+254712345678",
        "0712345678",
        "0712 345 678",
        "254712345678",
        "00254712345678",
        "+254-712-345-678",
    ],
)
def test_equivalent_kenyan_numbers_normalize_identically(raw):
    assert normalize_address(raw) == "tel:254712345678"


def test_alphanumeric_sender_ids_compare_case_insensitively():
    assert normalize_address("  MPesa  ") == normalize_address("mpesa") == "id:mpesa"


@pytest.mark.parametrize("raw", ["", "   ", "12", "---"])
def test_unusable_addresses_normalize_to_empty(raw):
    assert normalize_address(raw) == ""
    assert counterpart_hash(OWNER, raw) == ""


def test_hash_is_stable_per_owner_and_differs_between_owners():
    a = counterpart_hash(OWNER, "0712345678")
    assert a == counterpart_hash(OWNER, "+254 712 345 678")
    assert a != counterpart_hash(OTHER, "0712345678")
    assert len(a) == 64


def test_hash_does_not_contain_the_number_and_depends_on_the_key(settings):
    plain = counterpart_hash(OWNER, "0712345678")
    assert "712345678" not in plain
    settings.PHONE_HASH_KEY = "a-different-key-entirely-0123456789abcdef"
    assert counterpart_hash(OWNER, "0712345678") != plain
