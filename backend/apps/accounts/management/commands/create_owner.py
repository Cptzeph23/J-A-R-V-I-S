"""Create a user from the command line. There is no public registration endpoint.

The password is always read from an interactive prompt (never a CLI argument, which
would leak into shell history and the process list).
"""

from __future__ import annotations

import getpass
from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts.models import User, normalize_email
from apps.audit.recorder import record


class Command(BaseCommand):
    help = "Create a user account (prompts for the password)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--email", required=True)
        parser.add_argument("--timezone", default="Africa/Nairobi")
        parser.add_argument("--currency", default="KES")

    def handle(self, *args: Any, **options: Any) -> None:
        email = normalize_email(options["email"])
        if User.objects.filter(email=email).exists():
            raise CommandError("A user with that email already exists.")

        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Password (again): "):
            raise CommandError("Passwords do not match.")

        candidate = User(email=email)
        try:
            validate_password(password, user=candidate)
            user = User.objects.create_user(
                email,
                password,
                timezone=options["timezone"],
                default_currency=options["currency"],
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc

        record(
            "account.owner_created",
            owner_id=user.id,
            actor="system",
            target_type="user",
            target_id=user.id,
        )
        self.stdout.write(self.style.SUCCESS(f"Created user {user.email} ({user.id})."))
