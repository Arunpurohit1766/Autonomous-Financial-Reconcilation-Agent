"""State machine rules defining strictly allowed lifecycle transitions and RBAC authorization."""

from typing import Set
from app.domain.enums import JobStatus, ExceptionStatus, ApprovalStatus, ApprovalDecision, UserRole


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal status transition is attempted."""
    def __init__(self, entity_name: str, current_state: str, target_state: str):
        super().__init__(
            f"Invalid {entity_name} transition: cannot transition from {current_state} to {target_state}"
        )
        self.entity_name = entity_name
        self.current_state = current_state
        self.target_state = target_state


class UnauthorizedApprovalError(PermissionError):
    """Raised when an unauthorized actor attempts an approval decision."""
    pass


# 1. Reconciliation Job legal transitions
ALLOWED_JOB_TRANSITIONS: dict[JobStatus, Set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.PROCESSING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.PROCESSING: {
        JobStatus.WAITING_FOR_REVIEW,
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_EXCEPTIONS,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.WAITING_FOR_REVIEW: {
        JobStatus.PROCESSING,
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_EXCEPTIONS,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.COMPLETED: set(),
    JobStatus.COMPLETED_WITH_EXCEPTIONS: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}

# 2. Financial Exception legal transitions
ALLOWED_EXCEPTION_TRANSITIONS: dict[ExceptionStatus, Set[ExceptionStatus]] = {
    ExceptionStatus.OPEN: {
        ExceptionStatus.INVESTIGATING,
        ExceptionStatus.PENDING_REVIEW,
        ExceptionStatus.RESOLVED,
    },
    ExceptionStatus.INVESTIGATING: {
        ExceptionStatus.PENDING_REVIEW,
        ExceptionStatus.RESOLVED,
        ExceptionStatus.UNRESOLVED,
    },
    ExceptionStatus.PENDING_REVIEW: {
        ExceptionStatus.APPROVED,
        ExceptionStatus.REJECTED,
        ExceptionStatus.INVESTIGATING,
    },
    ExceptionStatus.APPROVED: {ExceptionStatus.RESOLVED},
    ExceptionStatus.REJECTED: {ExceptionStatus.UNRESOLVED},
    ExceptionStatus.RESOLVED: set(),
    ExceptionStatus.UNRESOLVED: {ExceptionStatus.INVESTIGATING},
}

# 3. Approval Request legal transitions
ALLOWED_APPROVAL_TRANSITIONS: dict[ApprovalStatus, Set[ApprovalStatus]] = {
    ApprovalStatus.PENDING: {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.CANCELLED,
    },
    ApprovalStatus.APPROVED: set(),
    ApprovalStatus.REJECTED: set(),
    ApprovalStatus.CANCELLED: set(),
}


def validate_job_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in ALLOWED_JOB_TRANSITIONS.get(current, set()):
        raise InvalidStateTransitionError("Job", current.value, target.value)


def validate_exception_transition(current: ExceptionStatus, target: ExceptionStatus) -> None:
    if target not in ALLOWED_EXCEPTION_TRANSITIONS.get(current, set()):
        raise InvalidStateTransitionError("Exception", current.value, target.value)


def validate_approval_transition(current: ApprovalStatus, target: ApprovalStatus) -> None:
    if target not in ALLOWED_APPROVAL_TRANSITIONS.get(current, set()):
        raise InvalidStateTransitionError("Approval", current.value, target.value)


def validate_approval_authorization(user_role: UserRole) -> None:
    allowed_roles = {UserRole.REVIEWER, UserRole.ADMIN}
    if user_role not in allowed_roles:
        raise UnauthorizedApprovalError(
            f"User role '{user_role.value}' is not authorized to submit approval decisions."
        )


def resolve_decision_transitions(
    decision: ApprovalDecision
) -> tuple[ApprovalStatus, ExceptionStatus]:
    if decision == ApprovalDecision.APPROVE:
        return ApprovalStatus.APPROVED, ExceptionStatus.APPROVED
    elif decision == ApprovalDecision.REJECT:
        return ApprovalStatus.REJECTED, ExceptionStatus.REJECTED
    elif decision == ApprovalDecision.REQUEST_INFO:
        return ApprovalStatus.PENDING, ExceptionStatus.INVESTIGATING
    raise ValueError(f"Unknown decision type: {decision}")
