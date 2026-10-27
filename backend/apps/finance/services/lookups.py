"""Owner-scoped lookups for ids supplied inside request bodies.

A path id that is not the caller's gives 404 (handled by the views). An id inside a body
that is not the caller's gives a 400 naming the field, and never reveals whether the id
exists for someone else.
"""

from __future__ import annotations

import uuid
from typing import Any

from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.common.models import OwnedModel


def resolve_owned[M: OwnedModel](model: type[M], owner: User, pk: uuid.UUID, field: str) -> M:
    found: Any = model.objects.for_owner(owner).filter(pk=pk).first()
    if found is None:
        raise ValidationError({field: ["Unknown id."]})
    result: M = found
    return result
