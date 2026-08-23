from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from state import (
    results_from_dashboard_state,
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
    starting_dashboard_state: dict[str, Any]
    starting_result: dict[str, Any] | None

    def with_evaluated_result(
        self,
        evaluated_result: dict[str, Any] | None,
    ) -> DashboardStateUpdate:
        return DashboardStateUpdate(self, evaluated_result)


@dataclass(frozen=True)
class DashboardStateUpdate:
    prepared: PreparedDashboardUpdate
    evaluated_result: dict[str, Any] | None


@dataclass(frozen=True)
class DashboardUpdateEffects:
    persist_dashboard_state: bool
    enqueue_status_comment: bool
    record_observations: bool
    clear_backfill_failure: bool


@dataclass(frozen=True)
class DashboardUpdateAcceptance:
    disposition: DashboardUpdateDisposition
    dashboard_state: dict[str, Any]
    accepted_result: dict[str, Any] | None
    effects: DashboardUpdateEffects

    @property
    def failed_result_rejected(self) -> bool:
        return self.disposition is DashboardUpdateDisposition.FAILED_RESULT_REJECTED


def prepare_dashboard_update(
    dashboard_state: dict[str, Any],
    open_pr_numbers: set[int],
    pr_number: int,
) -> PreparedDashboardUpdate:
    return PreparedDashboardUpdate(
        pr_number=pr_number,
        starting_dashboard_state=dashboard_state,
        starting_result=results_from_dashboard_state(
            dashboard_state,
            open_pr_numbers,
        ).get(pr_number),
    )


def _acceptance(
    disposition: DashboardUpdateDisposition,
    dashboard_state: dict[str, Any],
    pr_number: int,
    *,
    clear_backfill_failure: bool = False,
) -> DashboardUpdateAcceptance:
    changed = disposition is DashboardUpdateDisposition.APPLIED
    rejected = disposition is DashboardUpdateDisposition.FAILED_RESULT_REJECTED
    return DashboardUpdateAcceptance(
        disposition=disposition,
        dashboard_state=dashboard_state,
        accepted_result=(dashboard_state.get("prs") or {}).get(str(pr_number)),
        effects=DashboardUpdateEffects(
            persist_dashboard_state=changed,
            enqueue_status_comment=changed,
            record_observations=not rejected,
            clear_backfill_failure=clear_backfill_failure and not rejected,
        ),
    )


def accept_dashboard_update(
    update: DashboardStateUpdate,
    latest_dashboard_state: dict[str, Any] | None,
) -> DashboardUpdateAcceptance:
    prepared = update.prepared
    pr_number = prepared.pr_number
    dashboard_state = (
        latest_dashboard_state
        if latest_dashboard_state is not None
        else prepared.starting_dashboard_state
    )
    latest_result = (
        (latest_dashboard_state.get("prs") or {}).get(str(pr_number))
        if latest_dashboard_state is not None
        else prepared.starting_result
    )
    evaluated_result = update.evaluated_result

    if evaluated_result is None:
        if (
            latest_dashboard_state is not None
            and latest_result != prepared.starting_result
        ):
            return _acceptance(
                DashboardUpdateDisposition.CONCURRENT_UPDATE,
                dashboard_state,
                pr_number,
            )
        if latest_result is None:
            return _acceptance(
                DashboardUpdateDisposition.UNCHANGED,
                dashboard_state,
                pr_number,
            )
        return _acceptance(
            DashboardUpdateDisposition.APPLIED,
            update_dashboard_state_for_pr(dashboard_state, pr_number, None),
            pr_number,
        )

    clear_backfill_failure = not bool(evaluated_result.get("failed"))
    current_result = stored_result(evaluated_result)
    if latest_result == current_result:
        return _acceptance(
            DashboardUpdateDisposition.UNCHANGED,
            dashboard_state,
            pr_number,
            clear_backfill_failure=clear_backfill_failure,
        )
    if (
        latest_dashboard_state is not None
        and latest_result != prepared.starting_result
    ):
        return _acceptance(
            DashboardUpdateDisposition.CONCURRENT_UPDATE,
            dashboard_state,
            pr_number,
            clear_backfill_failure=clear_backfill_failure,
        )
    if evaluated_result.get("failed"):
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
