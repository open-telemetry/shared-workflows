"""Typed builders shared by pull request dashboard tests."""

from __future__ import annotations

from typing import Any

from dashboard_contracts import (
    DashboardFacts,
    DashboardRoute,
    DashboardState,
    EvaluationDiagnostics,
    EvaluationFailure,
    EvaluationSuccess,
    ReviewerSummary,
    StoredDashboardResult,
)


def dashboard_facts(**changes: Any) -> DashboardFacts:
    if "reviewers" in changes:
        changes["reviewers"] = tuple(
            reviewer
            if isinstance(reviewer, ReviewerSummary)
            else ReviewerSummary(**reviewer)
            for reviewer in changes["reviewers"]
        )
    return DashboardFacts().with_changes(**changes)


def evaluation_success(
    pr_number: int = 1,
    route: DashboardRoute | str = DashboardRoute.AUTHOR,
    *,
    facts: DashboardFacts | None = None,
    pr_title: str = "",
    pr_url: str | None = None,
    diagnostics: EvaluationDiagnostics | None = None,
    pending_actions: dict[str, Any] | None = None,
    top_level_history: dict[str, Any] | None = None,
) -> EvaluationSuccess:
    return EvaluationSuccess(
        pr_number=pr_number,
        pr_title=pr_title,
        pr_url=pr_url or f"https://example.test/pull/{pr_number}",
        route=DashboardRoute(route),
        facts=facts or DashboardFacts(),
        diagnostics=diagnostics or EvaluationDiagnostics(),
        pending_actions=pending_actions or {},
        top_level_history=top_level_history or {},
    )


def evaluation_failure(
    pr_number: int = 1,
    route: DashboardRoute | str = DashboardRoute.UNKNOWN,
    *,
    error: str = "failed",
    facts: DashboardFacts | None = None,
    pr_title: str = "",
    pr_url: str | None = None,
    diagnostics: EvaluationDiagnostics | None = None,
) -> EvaluationFailure:
    return EvaluationFailure(
        pr_number=pr_number,
        route=DashboardRoute(route),
        error=error,
        facts=facts,
        pr_title=pr_title,
        pr_url=pr_url or f"https://example.test/pull/{pr_number}",
        diagnostics=diagnostics or EvaluationDiagnostics(),
    )


def stored_dashboard_result(
    pr_number: int = 1,
    route: DashboardRoute | str = DashboardRoute.AUTHOR,
    *,
    facts: DashboardFacts | None = None,
    pr_url: str | None = None,
    top_level_history: dict[str, Any] | None = None,
) -> StoredDashboardResult:
    return StoredDashboardResult(
        pr_number=pr_number,
        pr_url=pr_url or f"https://example.test/pull/{pr_number}",
        route=DashboardRoute(route),
        facts=facts or DashboardFacts(),
        top_level_history=top_level_history or {},
    )


def dashboard_state(
    *results: StoredDashboardResult | EvaluationSuccess,
    initial_backfill_complete: bool = False,
) -> DashboardState:
    stored = tuple(
        result
        if isinstance(result, StoredDashboardResult)
        else StoredDashboardResult.from_evaluation(result)
        for result in results
    )
    return DashboardState(
        initial_backfill_complete=initial_backfill_complete,
        results=stored,
    )
