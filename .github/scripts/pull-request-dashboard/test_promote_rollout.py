from __future__ import annotations

import unittest

from promote_rollout import PromotionError, promoted_text

OLD_SHA = "1" * 40
NEW_SHA = "2" * 40
WORKFLOW = f"""name: Pull request dashboard

# uses: open-telemetry/shared-workflows/.github/workflows/pull-request-dashboard-repo.yml@<sha> # vX.Y.Z
#   code_ref: <sha> # vX.Y.Z
jobs:
  run-repo-dashboard-canary:
    uses: ./.github/workflows/pull-request-dashboard-repo.yml

  run-repo-dashboard-stable:
    uses: open-telemetry/shared-workflows/.github/workflows/pull-request-dashboard-repo.yml@{OLD_SHA} # v0.5.0
    with:
      repository: example
      code_ref: {OLD_SHA} # v0.5.0

  run-targeted-dashboard-stable:
    uses: open-telemetry/shared-workflows/.github/workflows/pull-request-dashboard-repo.yml@{OLD_SHA} # v0.5.0
    with:
      repository: example
      code_ref: {OLD_SHA} # v0.5.0

  run-head-sha-dashboard-stable:
    uses: open-telemetry/shared-workflows/.github/workflows/pull-request-dashboard-repo.yml@{OLD_SHA} # v0.5.0
    with:
      repository: example
      code_ref: {OLD_SHA} # v0.5.0

  notify:
    runs-on: ubuntu-latest
"""


class PromoteRolloutTest(unittest.TestCase):
    def test_updates_every_stable_pin_without_touching_examples_or_canary(self) -> None:
        promoted = promoted_text(WORKFLOW, "v0.6.0", NEW_SHA)

        self.assertEqual(3, promoted.count(f"@{NEW_SHA} # v0.6.0"))
        self.assertEqual(3, promoted.count(f"code_ref: {NEW_SHA} # v0.6.0"))
        self.assertNotIn(OLD_SHA, promoted)
        self.assertIn("uses: ./.github/workflows/pull-request-dashboard-repo.yml", promoted)
        self.assertIn("code_ref: <sha> # vX.Y.Z", promoted)

    def test_rejects_same_or_older_release(self) -> None:
        for release in ("v0.5.0", "v0.4.9"):
            with self.subTest(release=release), self.assertRaisesRegex(PromotionError, "must be newer"):
                promoted_text(WORKFLOW, release, NEW_SHA)

    def test_rejects_malformed_release_or_sha(self) -> None:
        cases = (("0.6.0", NEW_SHA), ("v0.06.0", NEW_SHA), ("v0.6.0-rc.1", NEW_SHA), ("v0.6.0", "ABC"))
        for release, sha in cases:
            with self.subTest(release=release, sha=sha), self.assertRaises(PromotionError):
                promoted_text(WORKFLOW, release, sha)

    def test_rejects_disagreement_within_a_stable_job(self) -> None:
        inconsistent = WORKFLOW.replace(
            f"code_ref: {OLD_SHA} # v0.5.0",
            f"code_ref: {'3' * 40} # v0.5.0",
            1,
        )

        with self.assertRaisesRegex(PromotionError, "uses and code_ref pins disagree"):
            promoted_text(inconsistent, "v0.6.0", NEW_SHA)

    def test_rejects_disagreement_between_stable_jobs(self) -> None:
        inconsistent = WORKFLOW.replace(
            f"@{OLD_SHA} # v0.5.0",
            f"@{'3' * 40} # v0.5.0",
            1,
        ).replace(
            f"code_ref: {OLD_SHA} # v0.5.0",
            f"code_ref: {'3' * 40} # v0.5.0",
            1,
        )

        with self.assertRaisesRegex(PromotionError, "stable jobs disagree"):
            promoted_text(inconsistent, "v0.6.0", NEW_SHA)

    def test_rejects_a_missing_stable_job(self) -> None:
        incomplete = WORKFLOW.replace("  run-head-sha-dashboard-stable:", "  removed-dashboard-job:")

        with self.assertRaisesRegex(PromotionError, "stable rollout job is missing"):
            promoted_text(incomplete, "v0.6.0", NEW_SHA)


if __name__ == "__main__":
    unittest.main()
