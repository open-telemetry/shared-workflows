import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from report_rate_limits import report_rate_limits


class ReportRateLimitsTest(unittest.TestCase):
    @patch("report_rate_limits.gh_api")
    def test_reports_rest_and_graphql_headroom(self, mock_gh_api) -> None:
        mock_gh_api.return_value = {
            "resources": {
                "core": {
                    "limit": 5000,
                    "used": 1250,
                    "remaining": 3750,
                    "reset": 1234,
                },
                "graphql": {
                    "limit": 5000,
                    "used": 4500,
                    "remaining": 500,
                    "reset": 1234,
                },
            }
        }

        output = io.StringIO()
        with redirect_stdout(output):
            report_rate_limits(20)

        mock_gh_api.assert_called_once_with("/rate_limit")
        self.assertIn("REST /rate_limit core:", output.getvalue())
        self.assertIn("75.0% remaining", output.getvalue())
        self.assertIn("REST /rate_limit graphql:", output.getvalue())
        self.assertIn("::warning title=REST /rate_limit graphql rate limit::", output.getvalue())
        self.assertIn("10.0% headroom", output.getvalue())

    @patch("report_rate_limits.gh_api")
    def test_warns_when_rate_limit_query_fails(self, mock_gh_api) -> None:
        mock_gh_api.side_effect = RuntimeError("REST unavailable")

        output = io.StringIO()
        with redirect_stdout(output), self.assertRaisesRegex(RuntimeError, "REST unavailable"):
            report_rate_limits(20)

        self.assertIn("::warning title=REST rate-limit query failed::REST unavailable", output.getvalue())


if __name__ == "__main__":
    unittest.main()
