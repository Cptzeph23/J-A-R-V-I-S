"""Failures the social module reports. Codes are stable; messages are safe to show."""

from __future__ import annotations

from rest_framework import status

from apps.common.errors import ApiError, ConflictError


class SocialNotConnectedError(ConflictError):
    default_code = "social_not_connected"
    default_detail = "Connect a Facebook Page first."


class SocialNotConfiguredError(ApiError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "provider_not_configured"
    default_detail = "Facebook is not set up on the server."


class ConnectDeniedError(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "oauth_denied"
    default_detail = "Access was not granted."


class InvalidConnectStateError(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid_oauth_state"
    default_detail = "This authorization link is invalid or has expired. Start again."


class NoPageAccessError(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "no_page_access"
    default_detail = "No Facebook Page that you can post to was found for this login."


class InvalidPostError(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid_post"
    default_detail = "The post is not valid."


class ContentChangedError(ConflictError):
    default_code = "content_changed"
    default_detail = "The post changed since you saw it. Review it again."


class WrongStateError(ConflictError):
    default_code = "post_state_conflict"
    default_detail = "The post is not in a state that allows this."


class ApprovalExpiredError(ConflictError):
    default_code = "approval_expired"
    default_detail = "The approval has expired. Approve the post again."


class NotApprovedError(ConflictError):
    default_code = "post_not_approved"
    default_detail = "The post has not been approved."


class DailyLimitError(ApiError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = "daily_post_limit"
    default_detail = "The daily post limit has been reached."


class PublishInProgressError(ConflictError):
    default_code = "publish_in_progress"
    default_detail = "This post is being published right now."


class UncertainOutcomeError(ConflictError):
    default_code = "outcome_uncertain"
    default_detail = (
        "It is not known whether this post was published. Check your Page, then reconcile or "
        "resolve it; publishing again now could create a duplicate."
    )
