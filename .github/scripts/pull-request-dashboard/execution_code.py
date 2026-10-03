from __future__ import annotations

import re
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

from execution_process import run_monitored
from process_queue_batch import LeaseMonitor, SCRIPT_DIR

CODE_PATH = ".github/scripts/pull-request-dashboard"
SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class ExecutionCode:
    ref: str
    script_dir: Path
    python: str


def stable_code_ref(workflow: Path) -> str:
    pins = re.findall(
        r"(?m)^      code_ref: ([0-9a-f]{40}) # v\d+\.\d+\.\d+$",
        workflow.read_text(encoding="utf-8"),
    )
    if len(pins) != 3 or len(set(pins)) != 1:
        raise ValueError("the stable rollout must have three identical immutable code pins")
    return pins[0]


class ExecutionCodeLoader:
    def __init__(
        self,
        root: Path,
        monitor: LeaseMonitor,
        env: dict[str, str],
        *,
        repository_root: Path = SCRIPT_DIR.parents[2],
    ) -> None:
        self.root = root
        self.monitor = monitor
        self.env = env
        self.repository_root = repository_root
        self.bundles: dict[str, ExecutionCode] = {}

    def load(self, ref: str) -> ExecutionCode:
        if SHA.fullmatch(ref) is None:
            raise ValueError("dashboard execution requires an immutable 40-character commit SHA")
        if ref in self.bundles:
            return self.bundles[ref]
        directory = self.root / ref
        directory.mkdir(parents=True)
        archive = directory / "code.tar"
        self._run(["git", "fetch", "--quiet", "--no-tags", "origin", ref])
        self._run(["git", "archive", "--format=tar", f"--output={archive}", ref, CODE_PATH])
        with tarfile.open(archive) as source:
            source.extractall(directory, filter="data")
        archive.unlink()
        script_dir = directory / ".github" / "scripts" / "pull-request-dashboard"
        environment = directory / "venv"
        self._run([sys.executable, "-m", "venv", str(environment)])
        python = str(
            environment / "Scripts" / "python.exe" if sys.platform == "win32"
            else environment / "bin" / "python"
        )
        self._run([python, "-m", "pip", "install", "--quiet", "-r", str(script_dir / "requirements.txt")])
        self._run(["npm", "ci", "--quiet", "--prefix", str(script_dir)])
        self._run([python, "-m", "copilot", "download-runtime"])
        result = ExecutionCode(ref, script_dir, python)
        self.bundles[ref] = result
        print(f"Prepared immutable dashboard execution code {ref}")
        return result

    def _run(self, command: list[str]) -> None:
        completed = run_monitored(
            command, self.monitor.assert_valid, cwd=self.repository_root, env=self.env
        )
        if completed.returncode:
            raise RuntimeError(
                f"dashboard code preparation failed: {command[0]}: {completed.stderr}"
            )
