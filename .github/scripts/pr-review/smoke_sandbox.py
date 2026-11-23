"""Exercise the real review sandbox on a disposable Linux runner without a model call."""

import os
import shutil
import tempfile
from pathlib import Path

from common import run
from sandbox import run_in_sandbox


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pr-review-smoke-", dir=os.environ["RUNNER_TEMP"]) as directory:
        root = Path(directory)
        checkout = root / "source"
        checkout.mkdir()
        run(["git", "init", "--quiet", "--template="], cwd=checkout)
        run([
            "git", "-c", "user.name=Smoke test", "-c", "user.email=smoke@example.test",
            "commit", "--quiet", "--allow-empty", "-m", "Fixture",
        ], cwd=checkout)
        run(["git", "checkout", "--quiet", "--detach"], cwd=checkout)

        inputs = root / "input"
        inputs.mkdir()
        shutil.copyfile(Path(__file__).with_name("launch-copilot.sh"), inputs / "launch-copilot.sh")
        (inputs / "prompt.txt").write_text("Sandbox smoke test\n", encoding="utf-8")

        stub = root / "stub" / "bin" / "copilot"
        stub.parent.mkdir(parents=True)
        stub.write_text(
            '#!/bin/bash\nset -eu\ntouch "$GITHUB_WORKSPACE/smoke-write"\necho "Copilot stub ran"\n',
            encoding="utf-8",
            newline="\n",
        )
        stub.chmod(0o755)
        os.environ.update(
            PATH=f"{stub.parent}{os.pathsep}{os.environ['PATH']}",
            GH_TOKEN="fixture-repository-token",
            GITHUB_TOKEN="fixture-workflow-token",
            ACTIONS_RUNTIME_TOKEN="fixture-runtime-token",
            ACTIONS_ID_TOKEN_REQUEST_TOKEN="fixture-oidc-token",
            COPILOT_GITHUB_TOKEN="fixture-copilot-token",
        )
        result = run_in_sandbox(checkout, inputs, [], phase="smoke")
        if result.returncode or "Copilot stub ran" not in result.stdout.splitlines():
            raise SystemExit(
                f"Sandbox smoke test did not complete successfully (exit {result.returncode}).\n{result.stderr[-4000:]}"
            )
        if (checkout / "smoke-write").exists():
            raise SystemExit("Sandbox write changed the seed checkout")
        print("Sandbox launcher passed its isolation checks and ran the Copilot stub.")


if __name__ == "__main__":
    main()
