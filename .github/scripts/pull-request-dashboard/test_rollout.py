"""Guards for the pull request dashboard staged rollout wiring.

The rollout splits every entry path into a canary job that runs dashboard code
from ``main`` and a stable job that runs it from the promoted rollout ref.
``uses`` cannot take an expression, so the ref and the canary membership list
are repeated across jobs; these tests keep the copies in sync so a promotion
cannot land half applied.
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
CONFIG = SCRIPT_DIR / "repositories.json"

REPO_WORKFLOW_PATH = ".github/workflows/pull-request-dashboard-repo.yml"
STABLE_USES = re.compile(
    r"uses:\s*open-telemetry/shared-workflows/" + re.escape(REPO_WORKFLOW_PATH) + r"@(\S+)"
)
LOCAL_USES = f"uses: ./{REPO_WORKFLOW_PATH}"
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

    def test_inline_canary_lists_match_the_workflow_env(self) -> None:
        targeted = [job for job in self.jobs if job.startswith("run-targeted-dashboard-")]
        self.assertTrue(targeted, "expected the targeted jobs to exist")
        for job in targeted:
            inline = re.findall(r"fromJSON\('(\[[^']*\])'\)", self.jobs[job])
            self.assertEqual(len(inline), 1, f"{job} does not inline the canary list exactly once")
            self.assertEqual(json.loads(inline[0]), self.canary, job)

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
            self.assertIn(LOCAL_USES, body)
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
                self.assertIn(LOCAL_USES, body, f"{job} calls an unexpected workflow")
                self.assertNotIn("code_ref:", body, f"{job} pins scripts but not the workflow")
                refs.add("")
                continue
            ref = match.group(1)
            refs.add(ref)
            self.assertIn(f"code_ref: {ref}", body, f"{job} runs scripts from a different ref")
        self.assertEqual(len(refs), 1, f"stable jobs disagree on the rollout ref: {sorted(refs)}")

    def test_repo_workflow_accepts_the_code_ref_input(self) -> None:
        body = REPO_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("code_ref:", body)
        self.assertIn("ref: ${{ inputs.code_ref }}", body)


if __name__ == "__main__":
    unittest.main()
