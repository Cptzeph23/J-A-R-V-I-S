import pytest

from apps.social.errors import InvalidPostError
from apps.social.services import content


def test_text_is_trimmed_and_newlines_normalized():
    assert content.clean_text("  hi\r\nthere \n") == "hi\nthere"


@pytest.mark.parametrize("bad", ["", "   ", "x" * (content.MAX_TEXT + 1), "a\x00b", "a\x1bb"])
def test_bad_text_is_refused(bad):
    with pytest.raises(InvalidPostError):
        content.clean_text(bad)


def test_exactly_the_maximum_is_allowed():
    assert len(content.clean_text("x" * content.MAX_TEXT)) == content.MAX_TEXT


@pytest.mark.parametrize(
    "bad",
    [
        "http://example.com",
        "ftp://example.com",
        "javascript:alert(1)",
        "https://user:pw@example.com",
        "https://",
        "https://exa mple.com",
        "https://example.com/" + "a" * content.MAX_LINK,
    ],
)
def test_bad_links_are_refused(bad):
    with pytest.raises(InvalidPostError):
        content.clean_link(bad)


def test_good_link_and_empty_link():
    assert content.clean_link(" https://example.com/a?b=1 ") == "https://example.com/a?b=1"
    assert content.clean_link("") == ""


def test_hash_binds_page_text_and_link():
    base = content.content_hash("p1", "hello", "")
    assert base == content.content_hash("p1", "hello", "")
    assert base != content.content_hash("p2", "hello", "")
    assert base != content.content_hash("p1", "hello!", "")
    assert base != content.content_hash("p1", "hello", "https://example.com")
    assert len(base) == 64


def test_comparable_ignores_whitespace_only():
    assert content.comparable("a  b\n c ") == content.comparable("a b c")
    assert content.comparable("a b") != content.comparable("ab")
