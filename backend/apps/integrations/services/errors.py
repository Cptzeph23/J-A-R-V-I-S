from rest_framework import status

from apps.common.errors import ApiError, ConflictError


class ProviderNotConfiguredError(ApiError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "provider_not_configured"
    default_detail = "This provider is not set up on the server."


class InvalidOAuthStateError(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid_oauth_state"
    default_detail = "This authorization link is invalid or has expired. Start again."


class OAuthDeniedError(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "oauth_denied"
    default_detail = "Access was not granted."


class ScopeNotGrantedError(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "scope_not_granted"
    default_detail = "The requested permission was not granted. Connect again and allow it."


class IntegrationUnusableError(ConflictError):
    default_code = "integration_unusable"
    default_detail = "This connection needs to be reconnected."


class SyncTooSoonError(ApiError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = "sync_too_soon"
    default_detail = "A sync ran a moment ago. Try again shortly."
