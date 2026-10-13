"""Guards for owned dashboard execution and release selection."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from promote_rollout import DEFAULT_CONFIG, stable_release

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "pull-request-dashboard.yml"
SWEEP_WORKFLOW = WORKFLOWS / "pull-request-dashboard-refresh-author-nudges.yml"
DRAIN_WORKFLOW = WORKFLOWS / "pull-request-dashboard-drain.yml"
DEPLOY_WORKFLOW = WORKFLOWS / "pull-request-dashboard-deploy-webhook.yml"


def job_blocks(text: str) -> dict[str, str]:
    lines = text.splitlines()
    blocks: dict[str, list[str]] = {}
    name: str | None = None
    for line in lines[lines.index("jobs:") + 1 :]:
        header = re.match(r"^ {2}([A-Za-z0-9_-]+):\s*$", line)
        if header:
            name = header.group(1)
            blocks[name] = []
        elif name is not None:
            blocks[name].append(line)
    return {job: "\n".join(body) for job, body in blocks.items()}


class RolloutWiringTest(unittest.TestCase):
    def test_dashboard_only_enqueues_or_queries_progress(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        jobs = job_blocks(text)
        self.assertEqual(set(jobs), {"enqueue"})
        self.assertNotIn("    if:", jobs["enqueue"])
        self.assertIn("id-token: write", jobs["enqueue"])
        self.assertIn("enqueue_dashboard.py", jobs["enqueue"])
        for input_name in ("repository", "pr_number", "head_sha", "trigger_event",
                           "request_item_key", "request_generation", "request_id"):
            self.assertIn(f"inputs.{input_name}", jobs["enqueue"])
        self.assertIn("group: pull-request-dashboard-enqueue-${{ github.run_id }}", text)
        self.assertNotIn("legacy", text)
        self.assertNotIn("matrix:", text)
        self.assertFalse((WORKFLOWS / "pull-request-dashboard-repo.yml").exists())
        self.assertFalse((SCRIPT_DIR / "action.yml").exists())

    def test_owned_drain_keeps_lane_ownership_and_immutable_canary_source(self) -> None:
        text = DRAIN_WORKFLOW.read_text(encoding="utf-8")
        jobs = job_blocks(text)
        self.assertEqual(set(jobs), {"owned-drain"})
        body = jobs["owned-drain"]
        self.assertIn("vars.PR_DASHBOARD_EXECUTION_MODE == 'owned'", body)
        self.assertIn("queue_lane:", text)
        self.assertIn("default: live", text)
        self.assertIn("PR_DASHBOARD_QUEUE_LANE: ${{ inputs.queue_lane || 'live' }}", body)
        self.assertNotIn("concurrency:", text)
        self.assertNotIn("actions: write", body)
        self.assertEqual(body.count("actions/checkout@"), 1)
        self.assertIn("timeout-minutes: 50", body)
        self.assertIn("CANARY_CODE_REF: ${{ github.sha }}", body)
        self.assertIn('--canary-code-ref "$CANARY_CODE_REF"', body)
        self.assertIn('--canary-repositories-json "$CANARY_REPOSITORIES"', body)
        self.assertNotIn("--owned", body)
        self.assertNotIn("--claims", body)
        self.assertIn("COPILOT_REVIEW_FALLBACK_TOKEN: ${{ secrets.COPILOT_REVIEW_FALLBACK_TOKEN }}", body)
        self.assertIn("COPILOT_GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", body)
        self.assertIn("copilot-requests: write", body)
        canary = re.search(r"^ {6}CANARY_REPOSITORIES: '(\[[^']*\])'$", body, re.MULTILINE)
        self.assertIsNotNone(canary)
        configured = {entry["name"] for entry in json.loads((SCRIPT_DIR / "repositories.json").read_text())}
        self.assertEqual(json.loads(canary.group(1)), ["opentelemetry-java-instrumentation", "shared-workflows"])
        self.assertTrue(set(json.loads(canary.group(1))) <= configured)

    def test_snapshot_environment_remains_available_to_pinned_processors(self) -> None:
        for path in (DRAIN_WORKFLOW, SWEEP_WORKFLOW, WORKFLOWS / "github-actions-queue-collector.yml"):
            with self.subTest(workflow=path.name):
                self.assertIn(
                    "DATA_BRANCH_SNAPSHOTS: ${{ vars.DATA_BRANCH_SNAPSHOTS }}",
                    path.read_text(encoding="utf-8"),
                )

    def test_collector_uses_the_checkout_sha_as_an_explicit_push_lease(self) -> None:
        body = (WORKFLOWS / "github-actions-queue-collector.yml").read_text(encoding="utf-8")
        self.assertIn('expected_sha=$(git -C "$data_dir" rev-parse HEAD)', body)
        self.assertIn('echo "expected_sha=$expected_sha" >> "$GITHUB_OUTPUT"', body)
        self.assertIn("EXPECTED_SHA: ${{ steps.data.outputs.expected_sha }}", body)
        self.assertIn("state_branch.py commit-data", body)
        self.assertIn('--expected-sha "$EXPECTED_SHA"', body)
        self.assertNotIn('push origin "HEAD:', body)

    def test_sdk_installation_remains_per_execution_ref(self) -> None:
        loader = (SCRIPT_DIR / "execution_code.py").read_text(encoding="utf-8")
        self.assertIn('"pip", "install", "--quiet", "-r"', loader)
        self.assertIn('[python, "-m", "copilot", "download-runtime"]', loader)
        body = job_blocks(DRAIN_WORKFLOW.read_text(encoding="utf-8"))["owned-drain"]
        self.assertIn("actions/setup-python@", body)
        self.assertRegex(body, r"(?m)^ +python-version: ['\"]3\.\d+['\"]$")
        dependencies = json.loads((SCRIPT_DIR / "package.json").read_text(encoding="utf-8"))
        self.assertNotIn("@github/copilot", dependencies["dependencies"])
        self.assertRegex((SCRIPT_DIR / "requirements.txt").read_text(), r"(?m)^github-copilot-sdk==\d+\.\d+\.\d+$")
        tests = (WORKFLOWS / "pull-request-dashboard-test.yml").read_text()
        self.assertIn("python -m pip install -r requirements.txt", tests)
        self.assertNotIn("download-runtime", tests)

    def test_reminder_writes_enqueue_and_matrix_is_read_only(self) -> None:
        jobs = job_blocks(SWEEP_WORKFLOW.read_text(encoding="utf-8"))
        self.assertIn("inputs.dry_run == false", jobs["enqueue"])
        self.assertIn("enqueue_dashboard.py --reminders", jobs["enqueue"])
        self.assertIn("id-token: write", jobs["enqueue"])
        self.assertIn("inputs.dry_run", jobs["resolve-targets"])
        self.assertNotIn("PR_DASHBOARD_EXECUTION_MODE", jobs["resolve-targets"])
        self.assertIn("permission-issues: read", jobs["sweep"])
        self.assertIn('refresh_author_nudges.py --repo "$TARGET_REPOSITORY" --dry-run', jobs["sweep"])
        self.assertNotIn("contents: write", jobs["sweep"])
        self.assertNotIn("legacy", SWEEP_WORKFLOW.read_text())

    def test_webhook_deployment_preserves_independent_pause_override(self) -> None:
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("EXECUTION_MODE: ${{ inputs.execution_mode || vars.PR_DASHBOARD_EXECUTION_MODE }}", text)
        self.assertIn('[[ "$EXECUTION_MODE" =~ ^(paused|owned)$ ]]', text)
        self.assertIn('set_netlify_dispatcher_env.sh PR_DASHBOARD_EXECUTION_MODE "$EXECUTION_MODE"', text)
        self.assertIn("environment: protected", text)
        self.assertNotIn("PR_DASHBOARD_QUEUE_MODE", text)
        self.assertNotIn("legacy", text)
        self.assertNotIn(".github/workflows/pull-request-dashboard.yml", text)
        self.assertNotIn("env:unset", text)
        self.assertIn('set_netlify_dispatcher_env.sh OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY_BASE64 "$dispatcher_private_key_base64" --secret', text)

    def test_official_promotion_edits_the_worker_configuration(self) -> None:
        sha, release = stable_release(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        self.assertRegex(sha, r"^[0-9a-f]{40}$")
        self.assertRegex(release, r"^v\d+\.\d+\.\d+$")
        text = (WORKFLOWS / "promote-pull-request-dashboard.yml").read_text(encoding="utf-8")
        self.assertIn("git add .github/scripts/pull-request-dashboard/stable-release.json", text)
        self.assertNotIn("git add .github/workflows/pull-request-dashboard.yml", text)
        self.assertIn("promote_rollout.py", text)
        self.assertIn("'.draft or .prerelease'", text)
        self.assertIn('git merge-base --is-ancestor "$sha" "$GITHUB_SHA"', text)
        self.assertIn('git rev-parse "$RELEASE^{commit}"', text)
        self.assertNotIn("permission-workflows: write", text)


if __name__ == "__main__":
    unittest.main()
