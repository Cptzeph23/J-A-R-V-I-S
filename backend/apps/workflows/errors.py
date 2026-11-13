from apps.common.errors import ConflictError


class WorkflowActiveError(ConflictError):
    default_code = "workflow_active"
    default_detail = "The assistant is still working on your previous message in this conversation."
