from rest_framework import status

from apps.common.errors import ApiError


class BudgetExceededError(ApiError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = "daily_budget_exceeded"
    default_detail = "The daily assistant usage limit has been reached."
