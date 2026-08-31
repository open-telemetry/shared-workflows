from __future__ import annotations

import unittest

from dashboard_test_support import dashboard_facts
from route_presentation import (
    ROUTE_ORDER,
    ROUTE_PRESENTATION,
    route_label,
    route_status_summary,
    unreported_gate_phrase,
)


class RoutePresentationTest(unittest.TestCase):
    def test_every_route_has_dashboard_and_status_presentation(self) -> None:
        self.assertEqual(list(ROUTE_PRESENTATION), ROUTE_ORDER)
        for route in ROUTE_ORDER:
            with self.subTest(route=route):
                self.assertTrue(route_label(route))
                waiting_on, next_step = route_status_summary(route)
                self.assertTrue(waiting_on)
                self.assertTrue(next_step)

    def test_author_section_follows_reviewers(self) -> None:
        reviewer_index = ROUTE_ORDER.index("approver")

        self.assertEqual(ROUTE_ORDER[reviewer_index + 1], "author")

    def test_author_status_does_not_mention_login(self) -> None:
        self.assertEqual(
            ("Author", "Address or respond to review feedback."),
            route_status_summary("author"),
        )

    def test_unrecognized_route_uses_unknown_presentation(self) -> None:
        self.assertEqual(route_label("unknown"), route_label("other"))
        self.assertEqual(route_status_summary("unknown"), route_status_summary("other"))

    def test_both_unreported_gates_are_named_in_one_phrase(self) -> None:
        self.assertEqual(
            "the required status checks and the Copilot review",
            unreported_gate_phrase(dashboard_facts(
                required_checks_settled=False,
                copilot_review_unreported=True,
            )),
        )


if __name__ == "__main__":
    unittest.main()