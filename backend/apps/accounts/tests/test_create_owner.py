from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import User
from apps.audit.models import AuditEntry
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

STRONG = "Tr0ub4dor&3-horse-staple"


def run(*args, passwords=(STRONG, STRONG)):
    with mock.patch("getpass.getpass", side_effect=list(passwords)):
        call_command("create_owner", *args)


def test_creates_user_and_audits():
    run("--email", "  New@Example.com ")
    user = User.objects.get(email="new@example.com")
    assert user.check_password(STRONG)
    assert AuditEntry.objects.filter(action="account.owner_created", owner_id=user.id).exists()


def test_accepts_timezone_and_currency():
    run("--email", "a@example.com", "--timezone", "UTC", "--currency", "USD")
    user = User.objects.get()
    assert (user.timezone, user.default_currency) == ("UTC", "USD")


def test_password_mismatch():
    with pytest.raises(CommandError, match="do not match"):
        run("--email", "a@example.com", passwords=(STRONG, "something-else-entirely"))
    assert not User.objects.exists()


@pytest.mark.parametrize("weak", ["short", "password1234", "12345678901234"])
def test_weak_passwords_are_rejected(weak):
    with pytest.raises(CommandError):
        run("--email", "a@example.com", passwords=(weak, weak))
    assert not User.objects.exists()


def test_password_similar_to_email_is_rejected():
    with pytest.raises(CommandError):
        run(
            "--email", "johnsmith@example.com", passwords=("johnsmith@example", "johnsmith@example")
        )


def test_duplicate_email():
    UserFactory(email="a@example.com")
    with pytest.raises(CommandError, match="already exists"):
        run("--email", "A@example.com")


@pytest.mark.parametrize(("flag", "value"), [("--timezone", "Mars/Olympus"), ("--currency", "XXX")])
def test_invalid_options(flag, value):
    with pytest.raises(CommandError):
        run("--email", "a@example.com", flag, value)
    assert not User.objects.exists()


def test_password_is_never_an_argument():
    with pytest.raises(CommandError, match=r"unrecognized|error"):
        call_command("create_owner", "--email", "a@example.com", "--password", STRONG)
