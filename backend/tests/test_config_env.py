import pytest
from django.core.exceptions import ImproperlyConfigured

from config.env import env_bool, env_int, env_list, env_str


def test_env_str_required_missing(monkeypatch):
    monkeypatch.delenv("X_VAR", raising=False)
    with pytest.raises(ImproperlyConfigured, match="X_VAR"):
        env_str("X_VAR")


def test_env_str_default_and_value(monkeypatch):
    monkeypatch.delenv("X_VAR", raising=False)
    assert env_str("X_VAR", "d") == "d"
    monkeypatch.setenv("X_VAR", "v")
    assert env_str("X_VAR", "d") == "v"


def test_env_str_allows_explicit_empty_value(monkeypatch):
    monkeypatch.setenv("X_VAR", "")
    assert env_str("X_VAR") == ""


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "Yes", "on"])
def test_env_bool_true(monkeypatch, raw):
    monkeypatch.setenv("X_BOOL", raw)
    assert env_bool("X_BOOL", False) is True


@pytest.mark.parametrize("raw", ["0", "false", "No", "OFF"])
def test_env_bool_false(monkeypatch, raw):
    monkeypatch.setenv("X_BOOL", raw)
    assert env_bool("X_BOOL", True) is False


def test_env_bool_default_and_invalid(monkeypatch):
    monkeypatch.delenv("X_BOOL", raising=False)
    assert env_bool("X_BOOL", True) is True
    monkeypatch.setenv("X_BOOL", "maybe")
    with pytest.raises(ImproperlyConfigured):
        env_bool("X_BOOL", True)


def test_env_int(monkeypatch):
    monkeypatch.delenv("X_INT", raising=False)
    assert env_int("X_INT", 5) == 5
    monkeypatch.setenv("X_INT", "42")
    assert env_int("X_INT", 5) == 42
    monkeypatch.setenv("X_INT", "4.2")
    with pytest.raises(ImproperlyConfigured):
        env_int("X_INT", 5)


def test_env_list(monkeypatch):
    monkeypatch.setenv("X_LIST", " a.com, b.com ,,c.com ")
    assert env_list("X_LIST") == ["a.com", "b.com", "c.com"]
    monkeypatch.delenv("X_LIST")
    assert env_list("X_LIST", ["z"]) == ["z"]
    with pytest.raises(ImproperlyConfigured):
        env_list("X_LIST")
