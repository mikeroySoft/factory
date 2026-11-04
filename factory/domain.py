"""Closed decision vocabularies; values are the existing wire spellings."""
from enum import StrEnum


class ManagerDecision(StrEnum):
    RETRY = "RETRY"
    REWRITE = "REWRITE"
    HUMAN = "HUMAN"
    CLOSE = "CLOSE"
    RESOLVE = "RESOLVE"
    APPROVE = "APPROVE"
    SPLIT = "SPLIT"
    ROUTE = "ROUTE"
    AUTHORIZE = "AUTHORIZE"
    FIX = "FIX"


class ReviewVerdict(StrEnum):
    APPROVE = "APPROVE"
    REVISE = "REVISE"
    ERROR = "ERROR"
    # GitHub review-result rows have always used this wire spelling.
    REQUEST_CHANGES = "REQUEST_CHANGES"


class EscalationCode(StrEnum):
    PROTECTED_PATHS = "protected_paths"
    BUDGET = "budget"
    GATE_FAILED = "gate_failed"
    WORKER_STUCK = "worker_stuck"
    PR_UNPUBLISHED = "pr_unpublished"
    PR_CHECKS_FAILED = "pr_checks_failed"
    REVIEW_REVISE = "review_revise"
    REVIEWER_UNAVAILABLE = "reviewer_unavailable"
    APPROVAL_DRIFT = "approval_drift"
    APPROVAL_MISSING = "approval_missing"
    CI_FAILED = "ci_failed"
    CI_RUNNER = "ci_runner"
    REFRESH_HEAD = "refresh_head"
    REFRESH_CONFLICT = "refresh_conflict"
    REFRESH_EMPTY = "refresh_empty"
    GH_FAILED = "gh_failed"
    REVIEW_INTAKE = "review_intake"
    MANAGER_FAILED = "manager_failed"
    MANAGER_EXHAUSTED = "manager_exhausted"
    MANAGER_HUMAN = "manager_human"
    PR_FEEDBACK = "pr_feedback"
    PR_STALE = "pr_stale"
    LEASE_REFUSED = "lease_refused"
    DISPATCHER_ERROR = "dispatcher_error"
    NO_CHANGE = "no_change"
    RISK_DISAGREEMENT = "risk_disagreement"


class LifecycleOutcome(StrEnum):
    COMPLETED = "completed"
    MECHANISM_FAILURE = "mechanism_failure"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"
    PRODUCT_FEEDBACK = "product_feedback"
    PROJECT_ESCALATION = "project_escalation"
    APPROVED = "approved"
    MERGED = "merged"
    REFRESHED = "refreshed"
    NOT_ADMITTED = "not_admitted"
    NOT_ELIGIBLE = "not_eligible"
