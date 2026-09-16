from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from queue_rollout import (
    QueueRolloutError,
    delivery_versions_from_source,
    queue_mode,
)
from state import current_delivery_versions

SCRIPT_DIR = Path(__file__).resolve().parent


class QueueRolloutTest(unittest.TestCase):
    def write_state(self, directory: str, name: str, source: str) -> Path:
        path = Path(directory) / name
        path.write_text(source, encoding="utf-8")
        return path

    def test_source_parser_matches_runtime_delivery_versions(self) -> None:
        self.assertEqual(
            delivery_versions_from_source(SCRIPT_DIR / "state.py"),
            dict(sorted(current_delivery_versions().items())),
        )

    def test_all_mode_requires_identical_delivery_versions(self) -> None:
        source = """
DASHBOARD_STATE_VERSION = 18
STATUS_COMMENT_REVISION: int = 20
"""
        with tempfile.TemporaryDirectory() as directory:
            current = self.write_state(directory, "current.py", source)
            stable = self.write_state(directory, "stable.py", source)

            self.assertEqual(queue_mode(current, stable), "all")

    def test_any_delivery_version_difference_uses_canary_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            current = self.write_state(
                directory,
                "current.py",
                "DASHBOARD_STATE_VERSION = 18\nSTATUS_COMMENT_REVISION = 20\n",
            )
            for name, stable_source in {
                "state": (
                    "DASHBOARD_STATE_VERSION = 17\n"
                    "STATUS_COMMENT_REVISION = 20\n"
                ),
                "revision": (
                    "DASHBOARD_STATE_VERSION = 18\n"
                    "STATUS_COMMENT_REVISION = 19\n"
                ),
                "missing": "DASHBOARD_STATE_VERSION = 18\n",
            }.items():
                with self.subTest(name=name):
                    stable = self.write_state(
                        directory,
                        f"stable-{name}.py",
                        stable_source,
                    )
                    self.assertEqual(queue_mode(current, stable), "canary")

    def test_invalid_delivery_versions_fail_closed(self) -> None:
        invalid_sources = (
            "",
            "DASHBOARD_STATE_VERSION = True\n",
            'DASHBOARD_STATE_VERSION = "18"\n',
            "DASHBOARD_STATE_VERSION = 18\nDASHBOARD_STATE_VERSION = 19\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, source in enumerate(invalid_sources):
                with self.subTest(source=source):
                    path = self.write_state(
                        directory,
                        f"invalid-{index}.py",
                        source,
                    )
                    with self.assertRaises(QueueRolloutError):
                        delivery_versions_from_source(path)


if __name__ == "__main__":
    unittest.main()
