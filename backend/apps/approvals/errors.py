from apps.common.errors import ConflictError


class ApprovalNotPendingError(ConflictError):
    default_code = "approval_not_pending"
    default_detail = "This approval has already been decided or is no longer valid."


class ApprovalExpiredError(ConflictError):
    default_code = "approval_expired"
    default_detail = "This approval expired. Ask the assistant to try again."


class ApprovalNotApprovedError(ConflictError):
    default_code = "approval_not_approved"
    default_detail = "This action has not been approved."


class ApprovalIntegrityError(ConflictError):
    """Stored action and its hash disagree, or the action changed after approval."""

    default_code = "approval_integrity"
    default_detail = "The approved action no longer matches what was approved."
