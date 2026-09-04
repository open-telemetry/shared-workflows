"""Typed in-memory contracts for pull request dashboard results and state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from classification_policy import ClassificationResult


class DashboardRoute(str, Enum):
    MAINTAINER = "maintainer"
    APPROVER = "approver"
    AUTHOR = "author"
    TRANSIENT_FAILURE = "transient-failure"
    UNKNOWN = "unknown"

    @property
    def is_failure(self) -> bool:
        return self in (
            DashboardRoute.TRANSIENT_FAILURE,
            DashboardRoute.UNKNOWN,
        )


@dataclass(frozen=True)
class DashboardCommandReply:
    comment_id: int
    kind: str
    user: str = ""
    subcommand: str = ""
    head_sha: str = ""
    route: DashboardRoute | None = None
    held_gates: str = ""
    since: str = ""

    def __post_init__(self) -> None:
        if self.comment_id <= 0:
            raise ValueError("dashboard command reply comment_id must be positive")
        if self.kind not in (
            "routed",
            "cleared_by_feedback",
            "unauthorized",
            "unknown_command",
        ):
            raise ValueError(f"unknown dashboard command reply kind: {self.kind!r}")
        if self.kind == "routed" and self.route is None:
            raise ValueError("routed dashboard command replies require a route")
        if (
            self.kind == "routed"
            and self.route is not None
            and self.route.is_failure
        ):
            raise ValueError(
                "routed dashboard command replies require a successful route"
            )
        if self.kind != "routed" and self.route is not None:
            raise ValueError("only routed dashboard command replies may include a route")


@dataclass(frozen=True)
class ReviewerSummary:
    login: str
    approved: bool = False
    approved_non_team: bool = False
    pending_review: bool = False
    changes_requested: bool = False
    open_thread: bool = False
    top_level_feedback: bool = False


@dataclass(frozen=True)
class DashboardFacts:
    author: str = ""
    assignees: tuple[str, ...] = ()
    head_sha: str = ""
    routing_input_fingerprint: str = ""
    copilot_request_fingerprint: str = ""
    dashboard_override_command_id: int = 0
    dashboard_override_command_user: str = ""
    dashboard_override_bound_command_id: int = 0
    dashboard_override_head_sha: str = ""
    dashboard_override_since: str = ""
    dashboard_override_cleared_by_feedback: bool = False
    dashboard_command_replies: tuple[DashboardCommandReply, ...] = ()
    copilot_review_requested: bool = False
    copilot_review_exists: bool = False
    copilot_review_stale: bool = False
    copilot_review_needed: bool = False
    is_maintenance_bot: bool = False
    author_can_act: bool = True
    is_draft: bool = False
    approval_count: int = 0
    conflicts: str = "unknown"
    created_at: str = ""
    last_activity_at: str = ""
    last_author_activity_at: str = ""
    last_approver_activity_at: str = ""
    ci_failing_count: int | None = None
    ci_failing_since: str | None = None
    ci_maintainer_action_required_count: int | None = None
    ci_pending_count: int | None = None
    non_blocking_check_failures: tuple[str, ...] = ()
    copilot_first_review_missing_since: str | None = None
    copilot_review_outstanding: bool = False
    copilot_review_unreported: bool = False
    copilot_review_request_needed: bool = False
    required_checks_settled: bool = False
    route_held_since: str | None = None
    route_hold_expired: bool = False
    route_held_for_gates: bool = False
    waiting_since: str = ""
    waiting_age_basis: str = ""
    author_nudge_episode_id: str | None = None
    author_action_review_thread_urls: tuple[str, ...] = ()
    author_action_top_level_feedback_urls: tuple[str, ...] = ()
    reviewers: tuple[ReviewerSummary, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "assignees", tuple(self.assignees))
        object.__setattr__(
            self,
            "dashboard_command_replies",
            tuple(self.dashboard_command_replies),
        )
        object.__setattr__(
            self,
            "non_blocking_check_failures",
            tuple(self.non_blocking_check_failures),
        )
        object.__setattr__(
            self,
            "author_action_review_thread_urls",
            tuple(self.author_action_review_thread_urls),
        )
        object.__setattr__(
            self,
            "author_action_top_level_feedback_urls",
            tuple(self.author_action_top_level_feedback_urls),
        )
        object.__setattr__(self, "reviewers", tuple(self.reviewers))

    def with_changes(self, **changes: Any) -> DashboardFacts:
        return replace(self, **changes)


def freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): freeze_json(item)
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")


def freeze_json_object(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return freeze_json(value or {})


def thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): thaw_json(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class EvaluationDiagnostics:
    review_threads: tuple[Mapping[str, Any], ...] = ()
    top_level_items: tuple[Mapping[str, Any], ...] = ()
    top_level_author_comment_items: tuple[Mapping[str, Any], ...] = ()
    review_thread_classifications: tuple[ClassificationResult, ...] = ()
    top_level_classifications: tuple[ClassificationResult, ...] = ()
    top_level_author_comment_classifications: tuple[ClassificationResult, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "review_threads",
            "top_level_items",
            "top_level_author_comment_items",
        ):
            values = getattr(self, name)
            object.__setattr__(
                self,
                name,
                tuple(freeze_json_object(value) for value in values),
            )
        for name in (
            "review_thread_classifications",
            "top_level_classifications",
            "top_level_author_comment_classifications",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class EvaluationSuccess:
    pr_number: int
    pr_title: str
    pr_url: str
    route: DashboardRoute
    facts: DashboardFacts
    diagnostics: EvaluationDiagnostics = field(default_factory=EvaluationDiagnostics)
    pending_actions: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    top_level_history: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if self.pr_number <= 0:
            raise ValueError("successful evaluation pr_number must be positive")
        if self.route.is_failure:
            raise ValueError("successful evaluations require a successful route")
        object.__setattr__(
            self,
            "pending_actions",
            freeze_json_object(self.pending_actions),
        )
        object.__setattr__(
            self,
            "top_level_history",
            freeze_json_object(self.top_level_history),
        )


@dataclass(frozen=True)
class EvaluationFailure:
    pr_number: int
    route: DashboardRoute
    error: str
    pr_title: str = ""
    pr_url: str = ""
    facts: DashboardFacts | None = None
    diagnostics: EvaluationDiagnostics = field(default_factory=EvaluationDiagnostics)

    def __post_init__(self) -> None:
        if self.pr_number <= 0:
            raise ValueError("failed evaluation pr_number must be positive")
        if not self.route.is_failure:
            raise ValueError("failed evaluations require a failure route")
        if not self.error:
            raise ValueError("failed evaluations require an error")


@dataclass(frozen=True)
class EvaluationDraft:
    pr_number: int
    pr_title: str
    pr_url: str

    def __post_init__(self) -> None:
        if self.pr_number <= 0:
            raise ValueError("draft evaluation pr_number must be positive")


EvaluationResult = EvaluationSuccess | EvaluationFailure | EvaluationDraft


@dataclass(frozen=True)
class StoredDashboardResult:
    pr_number: int
    pr_url: str
    route: DashboardRoute
    facts: DashboardFacts
    top_level_history: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if self.pr_number <= 0:
            raise ValueError("stored dashboard result pr_number must be positive")
        if self.route.is_failure:
            raise ValueError("failed evaluations cannot be stored")
        object.__setattr__(
            self,
            "top_level_history",
            freeze_json_object(self.top_level_history),
        )

    @classmethod
    def from_evaluation(
        cls,
        result: EvaluationSuccess,
    ) -> StoredDashboardResult:
        return cls(
            pr_number=result.pr_number,
            pr_url=result.pr_url,
            route=result.route,
            facts=result.facts,
            top_level_history=result.top_level_history,
        )


@dataclass(frozen=True)
class DashboardState:
    initial_backfill_complete: bool = False
    results: tuple[StoredDashboardResult, ...] = ()
    draft_pr_numbers: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        results = tuple(sorted(self.results, key=lambda result: result.pr_number))
        numbers = [result.pr_number for result in results]
        if len(numbers) != len(set(numbers)):
            raise ValueError("dashboard state contains duplicate pull requests")
        draft_pr_numbers = frozenset(self.draft_pr_numbers)
        if any(
            not isinstance(number, int)
            or isinstance(number, bool)
            or number <= 0
            for number in draft_pr_numbers
        ):
            raise ValueError("dashboard state draft PR numbers must be positive integers")
        if draft_pr_numbers.intersection(numbers):
            raise ValueError("dashboard state cannot route a draft pull request")
        object.__setattr__(self, "results", results)
        object.__setattr__(self, "draft_pr_numbers", draft_pr_numbers)

    @property
    def pr_numbers(self) -> frozenset[int]:
        return frozenset(result.pr_number for result in self.results)

    def result_for(self, pr_number: int) -> StoredDashboardResult | None:
        return next(
            (
                result
                for result in self.results
                if result.pr_number == pr_number
            ),
            None,
        )

    def is_draft(self, pr_number: int) -> bool:
        return pr_number in self.draft_pr_numbers

    def results_for(
        self,
        pr_numbers: set[int] | frozenset[int],
    ) -> tuple[StoredDashboardResult, ...]:
        return tuple(
            result
            for result in self.results
            if result.pr_number in pr_numbers
        )

    def with_result(
        self,
        pr_number: int,
        result: StoredDashboardResult | None,
    ) -> DashboardState:
        if result is not None and result.pr_number != pr_number:
            raise ValueError("stored result does not match the updated PR number")
        retained = tuple(
            existing
            for existing in self.results
            if existing.pr_number != pr_number
        )
        return replace(
            self,
            results=retained + ((result,) if result is not None else ()),
            draft_pr_numbers=self.draft_pr_numbers - {pr_number},
        )

    def with_draft(self, pr_number: int) -> DashboardState:
        if pr_number <= 0:
            raise ValueError("draft pull request number must be positive")
        return replace(
            self,
            results=tuple(
                result
                for result in self.results
                if result.pr_number != pr_number
            ),
            draft_pr_numbers=self.draft_pr_numbers | {pr_number},
        )

    def with_initial_backfill_complete(self) -> DashboardState:
        if self.initial_backfill_complete:
            return self
        return replace(self, initial_backfill_complete=True)
