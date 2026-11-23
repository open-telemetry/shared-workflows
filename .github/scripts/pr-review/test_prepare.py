import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import prepare


class ReviewBundleTest(unittest.TestCase):
    def test_added_line_starting_with_two_pluses_is_not_a_file_header(self):
        scope = prepare.parse_diff(
            "diff --git a/example.txt b/example.txt\n"
            "--- a/example.txt\n+++ b/example.txt\n@@ -0,0 +1,2 @@\n"
            "+++ text\n+more text\n"
        )
        self.assertEqual(list(scope), ["example.txt"])
        self.assertEqual(scope["example.txt"]["changed_lines"], [1, 2])

    def test_fetches_changed_files_from_the_head_repository(self):
        metadata = {
            "headRepositoryOwner": {"login": "contributor"},
            "headRepository": {"name": "repo"},
            "headRefOid": "head-sha",
        }
        source = "src/example.txt"
        instructions = ".github/instructions/example.instructions.md"
        scope = {
            source: {"changed_lines": [2], "hunks": [[1, 3]], "status": "modified"},
            instructions: {"changed_lines": [1], "hunks": [[1, 1]], "status": "added"},
            "removed.txt": {"changed_lines": [], "hunks": [], "status": "deleted"},
            "licenses/example.txt": {"changed_lines": [1], "hunks": [[1, 1]], "status": "added"},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(
                prepare, "read_file_excerpt", side_effect=lambda repo, ref, path: path
            ) as read_file:
                prepare.write_review_bundle(metadata, "the diff", scope, root)

            self.assertCountEqual(
                read_file.call_args_list,
                [
                    mock.call("contributor/repo", "head-sha", source),
                    mock.call("contributor/repo", "head-sha", instructions),
                    mock.call("contributor/repo", "head-sha", "licenses/example.txt"),
                ],
            )
            self.assertEqual(
                {path.name for path in root.iterdir()},
                {"files", "pr.diff", "diff-scope.json"},
            )
            self.assertEqual((root / "files" / source).read_text(encoding="utf-8"), source)
            self.assertEqual(
                (root / "files" / instructions).read_text(encoding="utf-8"), instructions
            )
            self.assertEqual((root / "pr.diff").read_text(encoding="utf-8"), "the diff")
            self.assertEqual(
                json.loads((root / "diff-scope.json").read_text(encoding="utf-8")), scope
            )

    def test_does_not_fetch_files_for_a_deletion_only_bundle(self):
        metadata = {
            "headRepositoryOwner": {"login": "contributor"},
            "headRepository": {"name": "repo"},
            "headRefOid": "head-sha",
        }
        scope = {"removed.txt": {"changed_lines": [], "hunks": [], "status": "deleted"}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(prepare, "read_file_excerpt") as read_file:
                prepare.write_review_bundle(metadata, "the diff", scope, root)

            read_file.assert_not_called()
            self.assertFalse((root / "files").exists())

    def test_fetch_failure_is_not_reported_as_a_prepared_bundle(self):
        metadata = {
            "headRepositoryOwner": {"login": "contributor"},
            "headRepository": {"name": "repo"},
            "headRefOid": "head-sha",
        }
        scope = {"example.txt": {"changed_lines": [1], "hunks": [[1, 1]], "status": "added"}}
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                prepare, "read_file_excerpt", side_effect=RuntimeError("content unavailable")
            ):
                with self.assertRaisesRegex(RuntimeError, "content unavailable"):
                    prepare.write_review_bundle(metadata, "the diff", scope, Path(directory))
            self.assertFalse((Path(directory) / "diff-scope.json").exists())

    def test_metadata_uses_explicit_target_and_checks_resolved_url(self):
        summary = prepare.Summary(pr=123)
        metadata = {"number": 123, "url": "https://github.com/org/repo/pull/123"}
        with mock.patch.object(prepare, "gh_json", return_value=metadata) as api:
            self.assertEqual(prepare.pr_metadata("org/repo", 123, summary), metadata)
            self.assertEqual(api.call_args.args[0][:6], ["pr", "view", "123", "--repo", "org/repo", "--json"])
            api.return_value = {**metadata, "url": "https://github.com/other/repo/pull/123"}
            with self.assertRaisesRegex(RuntimeError, "different PR target"):
                prepare.pr_metadata("org/repo", 123, summary)

    def test_diff_is_fetched_from_the_target_repository(self):
        metadata = {
            "state": "OPEN", "url": "https://github.com/org/repo/pull/123",
            "headRefOid": "head",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(prepare, "pr_metadata", return_value=metadata),
            mock.patch.object(prepare, "gh", return_value=mock.Mock(stdout="diff")) as gh,
            mock.patch.object(prepare, "write_review_bundle"),
        ):
            prepare.build_bundle("org/repo", 123, Path(directory), prepare.Summary(pr=123))
            self.assertEqual(gh.call_args.args[0], ["pr", "diff", "123", "--repo", "org/repo", "--color", "never"])


if __name__ == "__main__":
    unittest.main()
