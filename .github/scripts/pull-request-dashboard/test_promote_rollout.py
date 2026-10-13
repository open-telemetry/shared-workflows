from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from promote_rollout import PromotionError, main, promoted_text, stable_release

OLD_SHA = "1" * 40
NEW_SHA = "2" * 40
CONFIG = json.dumps({"sha": OLD_SHA, "release": "v0.5.0"})


class PromoteRolloutTest(unittest.TestCase):
    def test_updates_the_stable_release(self) -> None:
        self.assertEqual(
            json.loads(promoted_text(CONFIG, "v0.6.0", NEW_SHA)),
            {"sha": NEW_SHA, "release": "v0.6.0"},
        )

    def test_rejects_same_or_older_release(self) -> None:
        for release in ("v0.5.0", "v0.4.9"):
            with self.subTest(release=release), self.assertRaisesRegex(PromotionError, "must be newer"):
                promoted_text(CONFIG, release, NEW_SHA)

    def test_compares_versions_numerically(self) -> None:
        self.assertEqual(stable_release(promoted_text(CONFIG, "v0.10.0", NEW_SHA)), (NEW_SHA, "v0.10.0"))

    def test_rejects_malformed_release_or_sha(self) -> None:
        cases = (
            ("0.6.0", NEW_SHA), ("v0.06.0", NEW_SHA), ("v0.6.0-rc.1", NEW_SHA),
            ("v0.6.0", "ABC"), ("v0.6.0", "main"), ("v0.6.0", "A" * 40),
        )
        for release, sha in cases:
            with self.subTest(release=release, sha=sha), self.assertRaises(PromotionError):
                promoted_text(CONFIG, release, sha)

    def test_rejects_invalid_current_configuration(self) -> None:
        cases = [
            "not json", "[]", "null", "{}",
            json.dumps({"sha": OLD_SHA}),
            json.dumps({"sha": "main", "release": "v0.5.0"}),
            json.dumps({"sha": OLD_SHA, "release": "v0.05.0"}),
            json.dumps({"sha": OLD_SHA, "release": "v0.5.0-rc.1"}),
            json.dumps({"sha": OLD_SHA, "release": None}),
            json.dumps({"sha": None, "release": "v0.5.0"}),
            json.dumps({"sha": OLD_SHA, "release": "v0.5.0", "extra": True}),
            f'{{"sha":"{OLD_SHA}","sha":"{NEW_SHA}","release":"v0.5.0"}}',
            f'{{"sha":"{OLD_SHA}","release":"v0.5.0","release":"v0.4.0"}}',
        ]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(PromotionError):
                promoted_text(text, "v0.6.0", NEW_SHA)

    def test_cli_updates_only_the_selected_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stable-release.json"
            path.write_text(CONFIG, encoding="utf-8")
            with mock.patch("sys.argv", ["promote_rollout.py", "v0.6.0", NEW_SHA, "--config", str(path)]):
                main()
            self.assertEqual(stable_release(path.read_text(encoding="utf-8")), (NEW_SHA, "v0.6.0"))


if __name__ == "__main__":
    unittest.main()
