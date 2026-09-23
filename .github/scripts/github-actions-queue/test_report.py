from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from report import (
    ALL,
    GITHUB_HOSTED,
    SELF_HOSTED,
    build_report,
    classify_runner,
    load_report_state,
    write_report_state,
)


def record(
    queue_seconds,
    *,
    repository="example",
    labels=None,
    created_at="2026-09-18T12:34:56Z",
    collected_at="2026-09-18T13:00:00Z",
):
    return {
        "repository": repository,
        "job_created_at": created_at,
        "queue_seconds": queue_seconds,
        "runner_labels": labels if labels is not None else ["ubuntu-latest"],
        "collected_at": collected_at,
    }


def write_collection(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as output:
        for item in records:
            output.write(json.dumps(item))
            output.write("\n")


class RunnerClassificationTest(unittest.TestCase):
    def test_classifies_broad_self_hosted_patterns_case_insensitively(self):
        for label in (
            "self-hosted",
            "CNCF-ubuntu-32-128-arm",
            "oracle-vm-8cpu-32gb-x86-64",
            "oracle-bare-metal-64cpu",
            "ubuntu-24.04-s390x",
        ):
            with self.subTest(label=label):
                self.assertEqual(SELF_HOSTED, classify_runner([label]))

    def test_classifies_other_labels_as_github_hosted(self):
        for label in (
            "ubuntu-latest",
            "windows-2025",
            "otel-windows-latest-8-cores",
        ):
            with self.subTest(label=label):
                self.assertEqual(GITHUB_HOSTED, classify_runner([label]))

    def test_any_self_hosted_label_controls_the_category(self):
        self.assertEqual(
            SELF_HOSTED,
            classify_runner(["linux", "x64", "self-hosted"]),
        )


class ReportTest(unittest.TestCase):
    def test_builds_hourly_rollups_and_exact_percentiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            state_path = root / "report-state.json.gz"
            output = root / "report-data"
            write_collection(
                jobs / "date=2026-09-18" / "collection-1.jsonl.gz",
                [
                    record(1),
                    record(2),
                    record(10),
                    record(
                        20,
                        repository="other",
                        labels=["linux", "cncf-ubuntu-8-32-x86"],
                    ),
                ],
            )

            summary = build_report(jobs, state_path, output)

            self.assertEqual(1, summary["new_files"])
            self.assertEqual(4, summary["records"])
            self.assertEqual(4, summary["valid_records"])
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertNotIn("null_queue_records", manifest)
            self.assertNotIn("negative_queue_records", manifest)
            self.assertEqual("2026-09-18T12:00:00Z", manifest["latest_hour"])
            self.assertEqual(["ubuntu-latest"], manifest["labels"][GITHUB_HOSTED])
            self.assertEqual(
                ["cncf-ubuntu-8-32-x86", "linux"],
                manifest["labels"][SELF_HOSTED],
            )

            daily = json.loads(
                (output / "date=2026-09-18.json").read_text()
            )["series"]
            github_rollup = next(
                row
                for row in daily
                if row["host"] == GITHUB_HOSTED
                and row["repository"] == ALL
                and row["label"] == ALL
            )
            self.assertEqual(3, github_rollup["count"])
            self.assertEqual(2, github_rollup["p50"])
            self.assertEqual(8.4, github_rollup["p90"])
            self.assertEqual(9.2, github_rollup["p95"])
            self.assertEqual(9.84, github_rollup["p99"])

            self_hosted_rollup = next(
                row
                for row in daily
                if row["host"] == SELF_HOSTED
                and row["repository"] == ALL
                and row["label"] == ALL
            )
            self.assertEqual(1, self_hosted_rollup["count"])
            self.assertEqual(20, self_hosted_rollup["p95"])

            summaries = json.loads(
                (output / "date=2026-09-18.json").read_text()
            )["summaries"]
            github_summary = next(
                row
                for row in summaries
                if row["host"] == GITHUB_HOSTED
                and row["repository"] == ALL
                and row["label"] == ALL
            )
            self.assertEqual(
                [[1.0, 1], [2.0, 1], [10.0, 1]],
                github_summary["histogram"],
            )

    def test_builds_daily_histograms_across_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            output = root / "report-data"
            write_collection(
                jobs / "date=2026-09-18" / "collection-1.jsonl.gz",
                [
                    record(1, created_at="2026-09-18T12:00:00Z"),
                    record(9, created_at="2026-09-18T13:00:00Z"),
                ],
            )

            build_report(jobs, root / "state.json.gz", output)

            summaries = json.loads(
                (output / "date=2026-09-18.json").read_text()
            )["summaries"]
            github_summary = next(
                row
                for row in summaries
                if row["host"] == GITHUB_HOSTED
                and row["repository"] == ALL
                and row["label"] == ALL
            )
            self.assertEqual(
                [[1.0, 1], [9.0, 1]],
                github_summary["histogram"],
            )

    def test_incrementally_processes_each_collection_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            state_path = root / "report-state.json.gz"
            output = root / "report-data"
            write_collection(
                jobs / "date=2026-09-18" / "collection-1.jsonl.gz",
                [record(4)],
            )
            build_report(jobs, state_path, output)

            unchanged = build_report(jobs, state_path, output)
            self.assertEqual(0, unchanged["new_files"])
            self.assertEqual(1, unchanged["valid_records"])

            write_collection(
                jobs / "date=2026-09-18" / "collection-2.jsonl.gz",
                [record(8, created_at="2026-09-17T23:45:00-02:00")],
            )
            changed = build_report(jobs, state_path, output)

            self.assertEqual(1, changed["new_files"])
            self.assertEqual(2, changed["valid_records"])
            state = load_report_state(state_path)
            self.assertEqual(2, len(state["processed_files"]))
            self.assertTrue((output / "date=2026-09-18.json").exists())

    def test_all_label_rollup_counts_a_multi_label_job_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            output = root / "report-data"
            write_collection(
                jobs / "date=2026-09-18" / "collection-1.jsonl.gz",
                [record(5, labels=["ubuntu-latest", "x64"])],
            )

            build_report(jobs, root / "state.json.gz", output)

            rows = json.loads(
                (output / "date=2026-09-18.json").read_text()
            )["series"]
            all_labels = next(
                row
                for row in rows
                if row["repository"] == ALL and row["label"] == ALL
            )
            self.assertEqual(1, all_labels["count"])
            self.assertEqual(
                {"*", "ubuntu-latest", "x64"},
                {
                    row["label"]
                    for row in rows
                    if row["repository"] == ALL
                },
            )

    def test_rebuilds_state_when_classification_patterns_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json.gz"
            stale_state = load_report_state(state_path)
            stale_state["self_hosted_label_patterns"] = ["old-pattern"]
            stale_state["records"] = 50
            write_report_state(state_path, stale_state)
            write_collection(
                root / "jobs" / "date=2026-09-18" / "collection-1.jsonl.gz",
                [record(5)],
            )

            summary = build_report(root / "jobs", state_path, root / "report-data")

            self.assertEqual(1, summary["records"])
            self.assertEqual(1, summary["new_files"])

    def test_rejects_negative_queue_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_collection(
                root / "jobs" / "date=2026-09-18" / "collection-1.jsonl.gz",
                [record(-1)],
            )

            with self.assertRaisesRegex(
                ValueError,
                "queue_seconds must not be negative",
            ):
                build_report(
                    root / "jobs",
                    root / "state.json.gz",
                    root / "report-data",
                )

    def test_rejects_null_queue_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_collection(
                root / "jobs" / "date=2026-09-18" / "collection-1.jsonl.gz",
                [record(None)],
            )

            with self.assertRaisesRegex(
                ValueError,
                "queue_seconds must not be null",
            ):
                build_report(
                    root / "jobs",
                    root / "state.json.gz",
                    root / "report-data",
                )

    def test_rejects_non_numeric_queue_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_collection(
                root / "jobs" / "date=2026-09-18" / "collection-1.jsonl.gz",
                [record("slow")],
            )

            with self.assertRaisesRegex(
                ValueError,
                "queue_seconds must be a number",
            ):
                build_report(
                    root / "jobs",
                    root / "state.json.gz",
                    root / "report-data",
                )


class DashboardContractTest(unittest.TestCase):
    def test_dashboard_has_required_filters_and_theme(self):
        dashboard = Path(__file__).parents[3] / "github-actions-queue" / "dashboard"
        html = (dashboard / "index.html").read_text(encoding="utf-8")
        script = (dashboard / "dashboard.js").read_text(encoding="utf-8")
        stylesheet = (dashboard / "dashboard.css").read_text(encoding="utf-8")
        workflow = (
            Path(__file__).parents[2]
            / "workflows"
            / "github-actions-queue-collector.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('id="runner-host"', html)
        self.assertIn('value="github-hosted" selected', html)
        self.assertIn('id="runner-label"', html)
        self.assertIn('id="repository"', html)
        self.assertIn("--cp-accent", html)
        self.assertNotIn('id="log-scale"', html)
        self.assertIn("manifest.json", script)
        self.assertIn("date=${date}.json", script)
        self.assertIn("window.history.replaceState", script)
        self.assertIn('params.get("host")', script)
        self.assertIn('["label", elements.label]', script)
        self.assertIn('["repository", elements.repository]', script)
        self.assertIn('params.get("start")', script)
        self.assertIn('params.get("end")', script)
        self.assertIn('date.toISOString().slice(0, 10) === value', script)
        self.assertIn('url.searchParams.set("label"', script)
        self.assertIn('url.searchParams.delete("label")', script)
        self.assertIn('url.searchParams.delete("repository")', script)
        self.assertIn('["start", elements.start]', script)
        self.assertIn('["end", elements.end]', script)
        self.assertIn('url.searchParams.delete("range")', script)
        self.assertIn('id="start-date"', html)
        self.assertIn('id="end-date"', html)
        self.assertNotIn('id="time-range"', html)
        self.assertIn('id="range-p50"', html)
        self.assertIn('id="range-p90"', html)
        self.assertIn('id="range-p95"', html)
        self.assertIn('id="range-p99"', html)
        self.assertIn('id="hourly-data-body"', html)
        self.assertIn("renderAccessibleData();", script)
        self.assertIn(
            "elements.rangeP90.textContent = "
            "formatExactDuration(selectedSummary?.p90)",
            script,
        )
        self.assertIn("formatExactDuration(row.p99)", script)
        self.assertIn("Date.parse(row.hour)", script)
        self.assertIn("--cp-warning: #b45309", html)
        self.assertNotIn('class="method"', html)
        self.assertNotIn("negative timestamp anomalies", html)
        self.assertNotIn('id="exclusions"', html)
        self.assertNotIn("negative_queue_records", script)
        self.assertIn("width: min(calc(100% - 24px), 1180px);", stylesheet)
        self.assertIn(
            'dashboard_dir="$site_dir/github-actions-queue"',
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
