"""Guards for the pull request dashboard staged rollout wiring.

The rollout splits every entry path into a canary job that runs dashboard code
from the triggering commit and a stable job that runs it from the promoted
rollout ref. ``uses`` cannot take an expression and a job-level ``if`` cannot
read ``env``, so the ref and the canary membership list are repeated across
jobs; these tests keep the copies in sync so a promotion cannot land half
applied.
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

    def test_every_entry_path_has_both_channels(self) -> None:
        for prefix in ENTRY_PATHS:
            self.assertIn(f"{prefix}-canary", self.jobs)
            self.assertIn(f"{prefix}-stable", self.jobs)

    def test_canary_jobs_run_the_workflow_from_this_commit(self) -> None:
        for job, body in self.jobs.items():
            if not job.endswith("-canary"):
                continue
            self.assertRegex(body, LOCAL_USES)
            # Checking out anything other than the triggering commit would
            # split the canary workflow from the canary scripts.
            self.assertNotIn("code_ref:", body)

    def test_stable_jobs_pin_scripts_to_the_ref_they_are_called_at(self) -> None:
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
                self.assertNotIn("code_ref:", body, f"{job} pins scripts but not the workflow")
                refs.add("")
                continue
            ref = match.group(1)
            refs.add(ref)
            # Anchored so a commented out or partially edited value cannot pass.
            code_ref = re.compile(rf"^\s*code_ref: {re.escape(ref)}\s*(#.*)?$", re.MULTILINE)
            self.assertRegex(body, code_ref, f"{job} runs scripts from a different ref")
        self.assertEqual(len(refs), 1, f"stable jobs disagree on the rollout ref: {sorted(refs)}")

    def test_repo_workflow_accepts_the_code_ref_input(self) -> None:
        body = REPO_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("code_ref:", body)
        self.assertIn("ref: ${{ inputs.code_ref }}", body)

    def test_direct_publisher_uses_the_shared_repository_lock(self) -> None:
        body = REPO_WORKFLOW.read_text(encoding="utf-8")
        publish_job = job_blocks(body)["publish-dashboard"]
        self.assertEqual(body.count("acquire-publisher-lock"), 1)
        self.assertEqual(body.count("release-publisher-lock"), 1)
        self.assertIn("    timeout-minutes: 50", publish_job)
        self.assertIn(
            "if: always() && steps.publisher-lock.outcome == 'success'",
            body,
        )
        self.assertLess(body.index("acquire-publisher-lock"), body.index("delivery.py"))
        self.assertLess(body.index("publish_dashboard.py"), body.index("release-publisher-lock"))

    def test_reminder_sweep_uses_the_shared_repository_lock(self) -> None:
        body = SWEEP_WORKFLOW.read_text(encoding="utf-8")
        sweep_job = job_blocks(body)["sweep"]
        self.assertEqual(sweep_job.count("acquire-publisher-lock"), 1)
        self.assertEqual(sweep_job.count("release-publisher-lock"), 1)
        self.assertIn("      contents: write", sweep_job)
        self.assertIn(
            "if: always() && steps.publisher-lock.outcome == 'success'",
            sweep_job,
        )
        self.assertLess(
            sweep_job.index("acquire-publisher-lock"),
            sweep_job.index("refresh_author_nudges.py"),
        )
        self.assertLess(
            sweep_job.index("refresh_author_nudges.py"),
            sweep_job.index("release-publisher-lock"),
        )

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

    def test_webhook_deployment_automates_queue_rollout(self) -> None:
        body = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("      - .github/workflows/pull-request-dashboard.yml", body)
        self.assertNotIn("vars.PR_DASHBOARD_QUEUE_MODE", body)
        self.assertIn("queue_mode=canary", body)
        self.assertIn("queue_mode=all", body)
        self.assertIn("acquire-publisher-lock release-publisher-lock", body)
        canary_default = body.index("queue_mode=canary")
        stable_guard = body.index("stable_queue_ready=true")
        all_selection = body.index("queue_mode=all")
        environment_write = body.index(
            'env:set PR_DASHBOARD_QUEUE_MODE "$queue_mode"'
        )
        self.assertLess(canary_default, stable_guard)
        self.assertLess(stable_guard, all_selection)
        self.assertLess(all_selection, environment_write)


if __name__ == "__main__":
    unittest.main()
