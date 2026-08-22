from __future__ import annotations

from typing import Any

from utils import actor_login, is_copilot_reviewer_login


def role_for(login: str, author: str, reviewers: set[str]) -> str:
    if not login:
        return "outsider"
    low = login.lower()
    if low == author.lower():
        return "author"
    if low in reviewers:
        return "approver"
    if low.startswith("app/") or low.endswith("[bot]"):
        return "bot"
    return "outsider"


def reviewer_actor_login(obj: dict[str, Any] | None) -> str:
    login = actor_login(obj)
    if is_copilot_reviewer_login(login):
        return "copilot-pull-request-reviewer[bot]"
    return login


def is_substantive_activity(event: dict[str, Any]) -> bool:
    if event.get("is_merge_from_base_by_non_author"):
        return False
    if event.get("actor_role") == "bot":
        return False
    if event["kind"] == "review-state" and event.get("state") != "COMMENTED":
        return True
    return bool((event.get("body") or "").strip())
