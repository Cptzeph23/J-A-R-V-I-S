"""Describe the Bearer scheme in the OpenAPI document."""

from __future__ import annotations

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class JWTAuthenticationScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "apps.accounts.authentication.JWTAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
