"""Guards for the pull request dashboard staged rollout wiring.

The rollout splits every entry path into a canary job that runs dashboard code
from the triggering commit and a stable job that runs it from the promoted
rollout ref. ``uses`` cannot take an expression and a job-level ``if`` cannot
read ``env``, so the workflow ref and the canary membership list are repeated
across jobs; these tests keep the copies in sync.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pull-request-dashboard.yml"
REPO_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pull-request-dashboard-repo.yml"
SWEEP_WORKFLOW = (
    REPO_ROOT
    / ".github"
    / "workflows"
    / "pull-request-dashboard-refresh-author-nudges.yml"
)
DRAIN_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pull-request-dashboard-drain.yml"
DEPLOY_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "pull-request-dashboard-deploy-webhook.yml"
)
WEBHOOK = SCRIPT_DIR / "netlify" / "functions" / "github-webhook.mjs"
CONFIG = SCRIPT_DIR / "repositories.json"

REPO_WORKFLOW_PATH = ".github/workflows/pull-request-dashboard-repo.yml"
# Anchored to the job-level key so a commented out line cannot satisfy a guard.
STABLE_USES = re.compile(
    r"^ {4}uses:\s*open-telemetry/shared-workflows/" + re.escape(REPO_WORKFLOW_PATH) + r"@(\S+)",
    re.MULTILINE,
)
LOCAL_USES = re.compile(r"^ {4}uses:\s*\./" + re.escape(REPO_WORKFLOW_PATH) + r"\s*$", re.MULTILINE)
ENTRY_PATHS = ("run-repo-dashboard", "run-targeted-dashboard", "run-head-sha-dashboard")


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def job_blocks(text: str) -> dict[str, str]:
    lines = text.splitlines()
    blocks: dict[str, list[str]] = {}
    name: str | None = None
    for line in lines[lines.index("jobs:") + 1 :]:
        if re.match(r"^ {2}#", line):
            # A comment at job indentation introduces the jobs below it rather
            # than belonging to the job above it.
            continue
        header = re.match(r"^ {2}([A-Za-z0-9_-]+):\s*$", line)
        if header:
            name = header.group(1)
            blocks[name] = []
        elif name is not None:
            blocks[name].append(line)
    return {job: "\n".join(body) for job, body in blocks.items()}


def canary_repositories(text: str) -> list[str]:
    match = re.search(r"^ {2}CANARY_REPOSITORIES: '(\[[^']*\])'$", text, re.MULTILINE)
    assert match is not None, "CANARY_REPOSITORIES is missing from the workflow env"
    return json.loads(match.group(1))


class RolloutWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = workflow_text()
        self.jobs = job_blocks(self.text)
        self.canary = canary_repositories(self.text)

    def test_both_classifier_entry_paths_install_the_sdk_runtime(self) -> None:
        for path, requirements in (
            (REPO_WORKFLOW, '"$DASHBOARD_CODE/requirements.txt"'),
            (DRAIN_WORKFLOW, ".github/scripts/pull-request-dashboard/requirements.txt"),
        ):
            with self.subTest(workflow=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("actions/setup-python@", text)
                self.assertIn("python-version: '3.12'", text)
                self.assertIn(f"python -m pip install -r {requirements}", text)
                self.assertIn("python -m copilot download-runtime", text)
                self.assertLess(
                    text.index("python -m pip install"),
                    text.index("python -m copilot download-runtime"),
                )
                self.assertIn("copilot-requests: write", text)
                self.assertIn("COPILOT_GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", text)
                self.assertNotIn("node_modules/.bin", text)
        dependencies = json.loads((SCRIPT_DIR / "package.json").read_text(encoding="utf-8"))
        self.assertNotIn("@github/copilot", dependencies["dependencies"])
        self.assertIn(
            "github-copilot-sdk==1.0.14",
            (SCRIPT_DIR / "requirements.txt").read_text(encoding="utf-8"),
        )

    def test_unit_tests_install_sdk_without_downloading_runtime(self) -> None:
        text = (WORKFLOW.parent / "pull-request-dashboard-test.yml").read_text(encoding="utf-8")
        self.assertIn("python -m pip install -r requirements.txt", text)
        self.assertNotIn("download-runtime", text)

    def test_targeted_canary_job_inlines_the_workflow_canary_list(self) -> None:
        body = self.jobs["run-targeted-dashboard-canary"]
        inline = re.findall(r"fromJSON\('(\[[^']*\])'\)", body)
        self.assertEqual(len(inline), 1, "expected exactly one inlined canary list")
        self.assertEqual(json.loads(inline[0]), self.canary)

    def test_targeted_stable_job_derives_membership_from_the_canary_skip(self) -> None:
        body = self.jobs["run-targeted-dashboard-stable"]
        self.assertIn("needs: run-targeted-dashboard-canary", body)
        self.assertIn("needs.run-targeted-dashboard-canary.result == 'skipped'", body)
        # A second inlined list would reintroduce the copy this job avoids.
        self.assertNotIn("fromJSON('[", body)

    def test_canary_repositories_are_configured(self) -> None:
        configured = {entry["name"] for entry in json.loads(CONFIG.read_text(encoding="utf-8"))}
        for name in self.canary:
            self.assertIn(name, configured)

    def test_run_name_exposes_the_workflow_concurrency_group(self) -> None:
        lines = self.text.splitlines()
        run_name = lines[lines.index("run-name: >-") + 1].strip()
        concurrency = lines[lines.index("concurrency:") + 2].strip()
        self.assertEqual(run_name, concurrency)

    def test_every_entry_path_has_both_channels(self) -> None:
        for prefix in ENTRY_PATHS:
            self.assertIn(f"{prefix}-canary", self.jobs)
            self.assertIn(f"{prefix}-stable", self.jobs)

    def test_direct_head_refreshes_each_matching_pr(self) -> None:
        resolver = self.jobs["resolve-head-sha"]
        self.assertIn('gh api --paginate --slurp', resolver)
        self.assertIn('/pulls?state=open&per_page=100', resolver)
        self.assertIn('select(.state == "open" and .head.sha == $sha)', resolver)
        self.assertIn('pr_numbers: ${{ steps.trigger.outputs.pr_numbers }}', resolver)
        for channel in ("canary", "stable"):
            job = self.jobs[f"run-head-sha-dashboard-{channel}"]
            self.assertIn("needs.resolve-head-sha.outputs.pr_numbers != '[]'", job)
            self.assertIn(
                "pr_number: ${{ fromJSON(needs.resolve-head-sha.outputs.pr_numbers) }}",
                job,
            )
            self.assertIn("pr_number: ${{ matrix.pr_number }}", job)
            self.assertIn("max-parallel: 1", job)

    def test_canary_jobs_run_the_workflow_from_this_commit(self) -> None:
        for job, body in self.jobs.items():
            if not job.endswith("-canary"):
                continue
            self.assertRegex(body, LOCAL_USES)
            self.assertNotIn("code_ref:", body)

    def test_stable_jobs_share_one_pinned_workflow_ref(self) -> None:
        stable_jobs = [job for job in self.jobs if job.endswith("-stable")]
        self.assertTrue(stable_jobs)
        refs = set()
        for job in stable_jobs:
            body = self.jobs[job]
            match = STABLE_USES.search(body)
            if match is None:
                # Before the first promotion the stable jobs call the local
                # workflow, so both channels run the same code.
                self.assertRegex(body, LOCAL_USES, f"{job} calls an unexpected workflow")
                refs.add("")
                continue
            ref = match.group(1)
            refs.add(ref)
            code_ref = re.compile(rf"^\s*code_ref: {re.escape(ref)}\s*(#.*)?$", re.MULTILINE)
            self.assertRegex(body, code_ref, f"{job} passes the wrong compatibility ref")
        self.assertEqual(len(refs), 1, f"stable jobs disagree on the rollout ref: {sorted(refs)}")

    def test_repo_workflow_loads_code_from_its_own_commit(self) -> None:
        body = REPO_WORKFLOW.read_text(encoding="utf-8")
        action = (SCRIPT_DIR / "action.yml").read_text(encoding="utf-8")
        self.assertNotIn("ref: ${{ inputs.", body)
        self.assertIn("code_ref:", body)
        self.assertEqual(body.count("inputs.code_ref"), 0)
        self.assertEqual(body.count("actions/checkout@"), 2)
        self.assertEqual(body.count("ref: ${{ github.sha }}"), 2)
        self.assertNotIn("path: live-config", body)
        self.assertNotIn("sparse-checkout:", body)
        self.assertIn(
            "DASHBOARD_CONFIG: .github/scripts/pull-request-dashboard/repositories.json",
            body,
        )
        self.assertEqual(body.count("uses: $/.github/scripts/pull-request-dashboard"), 2)
        self.assertEqual(
            body.count("${{ steps.dashboard-code.outputs.path }}/.cache/classifications"),
            4,
        )
        self.assertEqual(
            body.count("DASHBOARD_CODE: ${{ steps.dashboard-code.outputs.path }}"),
            6,
        )
        self.assertNotIn('python3 "${{ steps.dashboard-code.outputs.path }}', body)
        self.assertEqual(body.count("steps.dashboard-code.outcome == 'success'"), 3)
        self.assertIn("path=$GITHUB_ACTION_PATH", action)

    def test_direct_publisher_uses_separate_delivery_state(self) -> None:
        body = REPO_WORKFLOW.read_text(encoding="utf-8")
        publish_job = job_blocks(body)["publish-dashboard"]
        self.assertNotIn("publisher-lock", publish_job)
        self.assertIn("otelbot/pull-request-dashboard-delivery", body)
        self.assertIn('--delivery-state-branch "$delivery_state_branch"', publish_job)
        self.assertIn("    timeout-minutes: 50", publish_job)

    def test_reminder_sweep_relies_on_repository_publisher_concurrency(self) -> None:
        body = SWEEP_WORKFLOW.read_text(encoding="utf-8")
        sweep_job = job_blocks(body)["sweep"]
        self.assertNotIn("publisher-lock", sweep_job)
        self.assertIn(
            "group: pull-request-dashboard-publish-${{ matrix.name }}",
            sweep_job,
        )
        self.assertIn("      contents: write", sweep_job)

    def test_queue_mode_canary_list_matches_the_rollout_canary_list(self) -> None:
        webhook = WEBHOOK.read_text(encoding="utf-8")
        match = re.search(
            r"const QUEUE_CANARY_REPOSITORIES = new Set\(\[(.*?)\]\);",
            webhook,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "webhook queue canary list is missing")
        webhook_canary = re.findall(r'"([^"]+)"', match.group(1))
        self.assertEqual(webhook_canary, self.canary)

    def test_queue_drain_uses_one_current_checkout(self) -> None:
        body = DRAIN_WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(body.count("actions/checkout@"), 1)
        self.assertNotIn("code_ref:", body)
        self.assertNotRegex(body, STABLE_USES)
        self.assertRegex(body, r"(?m)^    timeout-minutes: 50$")
        self.assertIn("DRAIN_PROCESSING_DEADLINE", body)
        self.assertIn("drain_queue.py", body)
        self.assertNotIn("actions/create-github-app-token@", body)
        self.assertIn("      actions: write", body)
        self.assertIn('--canary-repositories-json "$CANARY_REPOSITORIES"', body)
        drain_canary = re.search(
            r"^ {6}CANARY_REPOSITORIES: '(\[[^']*\])'$",
            body,
            re.MULTILINE,
        )
        self.assertIsNotNone(drain_canary)
        self.assertEqual(json.loads(drain_canary.group(1)), self.canary)

    def test_webhook_deployment_automates_queue_rollout(self) -> None:
        body = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("      - .github/workflows/pull-request-dashboard.yml", body)
        self.assertNotIn("vars.PR_DASHBOARD_QUEUE_MODE", body)
        self.assertIn("queue_mode=all", body)
        self.assertNotIn("stable_queue_ready", body)
        self.assertIn("    environment: protected", body)
        self.assertNotIn("env:unset", body)
        self.assertLess(
            body.index("queue_mode=all"),
            body.index('set_netlify_dispatcher_env.sh PR_DASHBOARD_QUEUE_MODE "$queue_mode"'),
        )
        self.assertIn(
            'set_netlify_dispatcher_env.sh OTELBOT_SHARED_WORKFLOWS_CLIENT_ID "$OTELBOT_SHARED_WORKFLOWS_CLIENT_ID"',
            body,
        )
        self.assertIn(
            'set_netlify_dispatcher_env.sh OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY_BASE64 "$dispatcher_private_key_base64" --secret',
            body,
        )
        self.assertIn(
            ".github/scripts/pull-request-dashboard/set_netlify_dispatcher_env.sh",
            DEPLOY_WORKFLOW.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
