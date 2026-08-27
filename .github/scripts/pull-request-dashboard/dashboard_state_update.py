from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from dashboard_contracts import (
    DashboardState,
    EvaluationDraft,
    EvaluationFailure,
    EvaluationResult,
    StoredDashboardResult,
)
from state import (
    stored_result,
    update_dashboard_state_for_pr,
)


class DashboardUpdateDisposition(Enum):
    APPLIED = "applied"
    UNCHANGED = "unchanged"
    CONCURRENT_UPDATE = "concurrent_update"
    FAILED_RESULT_REJECTED = "failed_result_rejected"


@dataclass(frozen=True)
class PreparedDashboardUpdate:
    pr_number: int
    starting_dashboard_state: DashboardState
    starting_result: StoredDashboardResult | None
    starting_is_draft: bool

    def with_evaluated_result(
        self,
        evaluated_result: EvaluationResult | None,
    ) -> DashboardStateUpdate:
        return DashboardStateUpdate(self, evaluated_result)


@dataclass(frozen=True)
class DashboardStateUpdate:
    prepared: PreparedDashboardUpdate
    evaluated_result: EvaluationResult | None


@dataclass(frozen=True)
class DashboardUpdateEffects:
    persist_dashboard_state: bool
    enqueue_status_comment: bool
    record_observations: bool
    clear_backfill_failure: bool


@dataclass(frozen=True)
class DashboardUpdateAcceptance:
    disposition: DashboardUpdateDisposition
    dashboard_state: DashboardState
    accepted_result: StoredDashboardResult | None
    effects: DashboardUpdateEffects

    @property
    def failed_result_rejected(self) -> bool:
        return self.disposition is DashboardUpdateDisposition.FAILED_RESULT_REJECTED


def prepare_dashboard_update(
    dashboard_state: DashboardState,
    open_pr_numbers: set[int],
    pr_number: int,
) -> PreparedDashboardUpdate:
    starting_result = (
        dashboard_state.result_for(pr_number)
        if pr_number in open_pr_numbers
        else None
    )
    starting_is_draft = (
        dashboard_state.is_draft(pr_number)
        if pr_number in open_pr_numbers
        else False
    )
    return PreparedDashboardUpdate(
        pr_number=pr_number,
        starting_dashboard_state=dashboard_state,
        starting_result=starting_result,
        starting_is_draft=starting_is_draft,
    )


def _acceptance(
    disposition: DashboardUpdateDisposition,
    dashboard_state: DashboardState,
    pr_number: int,
    *,
    clear_backfill_failure: bool = False,
    enqueue_status_comment: bool | None = None,
    persist_dashboard_state: bool | None = None,
) -> DashboardUpdateAcceptance:
    changed = disposition is DashboardUpdateDisposition.APPLIED
    rejected = disposition is DashboardUpdateDisposition.FAILED_RESULT_REJECTED
    return DashboardUpdateAcceptance(
        disposition=disposition,
        dashboard_state=dashboard_state,
        accepted_result=dashboard_state.result_for(pr_number),
        effects=DashboardUpdateEffects(
            persist_dashboard_state=(
                changed
                if persist_dashboard_state is None
                else persist_dashboard_state
            ),
            enqueue_status_comment=(
                changed
                if enqueue_status_comment is None
                else enqueue_status_comment
            ),
            record_observations=not rejected,
            clear_backfill_failure=clear_backfill_failure,
        ),
    )


def accept_dashboard_update(
    update: DashboardStateUpdate,
    latest_dashboard_state: DashboardState | None,
) -> DashboardUpdateAcceptance:
    prepared = update.prepared
    pr_number = prepared.pr_number
    dashboard_state = (
        latest_dashboard_state
        if latest_dashboard_state is not None
        else prepared.starting_dashboard_state
    )
    latest_result = dashboard_state.result_for(pr_number)
    latest_is_draft = dashboard_state.is_draft(pr_number)
    slot_changed = (
        latest_result != prepared.starting_result
        or latest_is_draft != prepared.starting_is_draft
    )
    evaluated_result = update.evaluated_result

    if evaluated_result is None:
        if latest_dashboard_state is not None and slot_changed:
            return _acceptance(
                DashboardUpdateDisposition.CONCURRENT_UPDATE,
                dashboard_state,
                pr_number,
            )
        if latest_result is None and not latest_is_draft:
            return _acceptance(
                DashboardUpdateDisposition.UNCHANGED,
                dashboard_state,
                pr_number,
                enqueue_status_comment=True,
            )
        return _acceptance(
            DashboardUpdateDisposition.APPLIED,
            update_dashboard_state_for_pr(dashboard_state, pr_number, None),
            pr_number,
        )

    if isinstance(evaluated_result, EvaluationDraft):
        if latest_dashboard_state is not None and slot_changed:
            return _acceptance(
                DashboardUpdateDisposition.CONCURRENT_UPDATE,
                dashboard_state,
                pr_number,
                clear_backfill_failure=True,
            )
        accepted_state = dashboard_state.with_draft(pr_number)
        state_changed = accepted_state != dashboard_state
        return _acceptance(
            (
                DashboardUpdateDisposition.APPLIED
                if state_changed
                else DashboardUpdateDisposition.UNCHANGED
            ),
            accepted_state,
            pr_number,
            clear_backfill_failure=True,
            enqueue_status_comment=True,
            persist_dashboard_state=state_changed,
        )

    failed = isinstance(evaluated_result, EvaluationFailure)
    clear_backfill_failure = not failed
    current_result = (
        None
        if failed
        else stored_result(evaluated_result)
    )
    if (
        latest_dashboard_state is not None
        and current_result is not None
        and latest_result == current_result
    ):
        return _acceptance(
            DashboardUpdateDisposition.UNCHANGED,
            dashboard_state,
            pr_number,
            clear_backfill_failure=clear_backfill_failure,
        )
    if latest_dashboard_state is not None and slot_changed:
        return _acceptance(
            DashboardUpdateDisposition.CONCURRENT_UPDATE,
            dashboard_state,
            pr_number,
            clear_backfill_failure=clear_backfill_failure,
        )
    if failed:
        return _acceptance(
            DashboardUpdateDisposition.FAILED_RESULT_REJECTED,
            dashboard_state,
            pr_number,
        )
    return _acceptance(
        DashboardUpdateDisposition.APPLIED,
        update_dashboard_state_for_pr(
            dashboard_state,
            pr_number,
            evaluated_result,
        ),
        pr_number,
        clear_backfill_failure=True,
    )
