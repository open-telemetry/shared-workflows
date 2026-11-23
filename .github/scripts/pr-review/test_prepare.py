import tempfile
import unittest
from pathlib import Path
from unittest import mock

import common
import prepare


class ReviewBundleTest(unittest.TestCase):
    def test_right_side_paths_handle_git_quoting_and_header_delimiters(self):
        for header, path in (
            (r'"b/caf\303\251.txt"', "caf\u00e9.txt"),
            (r'"b/with\ttab\"quote.txt"' + "\t", 'with\ttab"quote.txt'),
            ("b/with space.txt\t", "with space.txt"),
        ):
            with self.subTest(header=header):
                scope = prepare.parse_diff(
                    f"diff --git a/file b/file\n--- /dev/null\n+++ {header}\n@@ -0,0 +1 @@\n+text\n"
                )
                self.assertEqual(scope, {path: {"changed_lines": [1], "hunks": [(1, 1)]}})

    def test_added_line_starting_with_two_pluses_is_not_a_file_header(self):
        scope = prepare.parse_diff(
            "diff --git a/example.txt b/example.txt\n"
            "--- a/example.txt\n+++ b/example.txt\n@@ -0,0 +1,2 @@\n"
            "+++ text\n+more text\n"
        )
        self.assertEqual(list(scope), ["example.txt"])
        self.assertEqual(scope["example.txt"]["changed_lines"], [1, 2])

    def test_metadata_uses_explicit_target_and_checks_resolved_url(self):
        metadata = {"number": 123, "html_url": "https://github.com/org/repo/pull/123"}
        with mock.patch.object(common, "gh_json", return_value=metadata) as api:
            self.assertEqual(common.pr_metadata("org/repo", 123), metadata)
            api.assert_called_once_with(["api", "repos/org/repo/pulls/123"])
            api.return_value = {**metadata, "html_url": "https://github.com/other/repo/pull/123"}
            with self.assertRaisesRegex(RuntimeError, "different PR target"):
                common.pr_metadata("org/repo", 123)

    def test_bundle_writes_only_the_diff_from_the_target_repository(self):
        metadata = {
            "state": "open", "html_url": "https://github.com/org/repo/pull/123",
            "head": {"sha": "head"},
        }
        diff = "diff --git a/example.txt b/example.txt\n+++ b/example.txt\n@@ -0,0 +1 @@\n+text\n"
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(prepare, "pr_metadata", return_value=metadata),
            mock.patch.object(prepare, "gh", return_value=mock.Mock(stdout=diff)) as gh,
        ):
            bundle = Path(directory)
            result, scope = prepare.build_bundle("org/repo", 123, bundle)
            self.assertEqual(result, metadata)
            self.assertEqual(scope, {"example.txt": {"changed_lines": [1], "hunks": [(1, 1)]}})
            self.assertEqual({path.name for path in bundle.iterdir()}, {"pr.diff"})
            self.assertEqual((bundle / "pr.diff").read_text(encoding="utf-8"), diff)
            gh.assert_called_once_with([
                "api", "-H", "Accept: application/vnd.github.diff", "repos/org/repo/pulls/123",
            ])


if __name__ == "__main__":
    unittest.main()
