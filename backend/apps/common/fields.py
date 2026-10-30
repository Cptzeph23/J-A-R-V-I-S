"""Shared serializer fields."""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone
from rest_framework import serializers


class StrictDateTimeField(serializers.DateTimeField):
    """Rejects datetimes without a UTC offset instead of guessing the server's timezone."""

    def enforce_timezone(self, value: datetime) -> datetime:
        if timezone.is_naive(value):
            raise serializers.ValidationError("Must include a UTC offset, for example Z or +03:00.")
        return value
