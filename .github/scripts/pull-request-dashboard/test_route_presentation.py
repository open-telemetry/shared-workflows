from __future__ import annotations

import unittest

from route_presentation import ROUTE_ORDER, ROUTE_PRESENTATION, route_label, route_status


class RoutePresentationTest(unittest.TestCase):
    def test_every_route_has_dashboard_and_status_presentation(self) -> None:
        self.assertEqual(list(ROUTE_PRESENTATION), ROUTE_ORDER)
        for route in ROUTE_ORDER:
            with self.subTest(route=route):
                self.assertTrue(route_label(route))
                self.assertTrue(route_status(route))

    def test_author_status_includes_effective_author(self) -> None:
        self.assertEqual(
            "Waiting on @alice to address or respond to review feedback.",
            route_status("author", "@alice"),
        )

    def test_unrecognized_route_uses_unknown_presentation(self) -> None:
        self.assertEqual(route_label("unknown"), route_label("other"))
        self.assertEqual(route_status("unknown"), route_status("other"))


if __name__ == "__main__":
    unittest.main()