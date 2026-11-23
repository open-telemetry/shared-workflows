"""Run Copilot against a public snapshot with read-only tools."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def response(events, returncode):
    answer = ""
    error = False
    for line in events.splitlines():
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        data = event.get("data") or {}
        if event.get("type") == "session.error":
            error = True
        if event.get("type") == "assistant.message" and data.get("content", "").strip():
            answer = data["content"].strip()
    if returncode or error or not answer:
        raise ValueError(f"Copilot did not complete successfully (firewall exit {returncode})")
    answer = re.sub(r"\A```(?:json)?[ \t]*\n(.*)\n```\Z", r"\1", answer, flags=re.DOTALL)
    return json.loads(answer)


def ask(checkout, prompt):
    copilot = shutil.which("copilot")
    awf = shutil.which("awf")
    if not copilot or not awf:
        raise ValueError("Copilot CLI or Agentic Workflow Firewall is unavailable")
    root = Path(tempfile.mkdtemp(prefix="instruction-audit-", dir=os.environ["RUNNER_TEMP"]))
    bundle = root / "input"
    bundle.mkdir()
    (bundle / "prompt.txt").write_text(prompt, encoding="utf-8")
    shutil.copyfile(Path(__file__).with_name("launch-copilot.sh"), bundle / "launch-copilot.sh")
    home = root / "home"
    home.mkdir()
    config = root / "awf.json"
    config.write_text(json.dumps({
        "network": {"isolation": True, "allowDomains": ["github.com", "release-assets.githubusercontent.com"]},
        "apiProxy": {"enabled": True, "modelFallback": {"enabled": False}},
        "chroot": {"identity": {"home": str(home)}},
    }), encoding="utf-8")
    env = {key: os.environ[key] for key in ("LANG", "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT",
                                          "COPILOT_GITHUB_TOKEN") if key in os.environ}
    env.update(HOME=str(home), GITHUB_WORKSPACE=str(checkout))
    cli_dir = Path(copilot).parent.parent
    capture = root / "output"
    result = subprocess.run([
        "/usr/bin/sudo", "--non-interactive", f"--preserve-env={','.join(env)}",
        str(Path(awf).resolve()), "--config", str(config), "--work-dir", str(root / "work"),
        "--image-tag", os.environ["AWF_IMAGE_TAG"], "--container-workdir", str(checkout),
        "--mount", f"{cli_dir}:{cli_dir}:ro", "--mount", f"{bundle}:{bundle}:ro",
        "--env", "GITHUB_COPILOT_PROMPT_MODE_EXTENSIONS=false",
        "--capture-command-output", str(capture), "--agent-timeout", "18",
        "--log-level", "info", "--",
        "/bin/bash", str(bundle / "launch-copilot.sh"), str(checkout), str(bundle), str(config), copilot,
        "--model", "gpt-6.1-sol", "--reasoning-effort", "high", "--output-format", "json", "--no-ask-user",
        "--no-auto-update", "--no-custom-instructions", "--disable-builtin-mcps", "--allow-all-paths",
        "--available-tools", "view", "glob", "grep", "--allow-tool", "view", "glob", "grep",
    ], cwd=checkout, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1200,
       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = capture / "attempt-001.stdout.bin"
    if not output.is_file() or output.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"Copilot output is missing or too large (firewall exit {result.returncode})")
    return response(output.read_text(encoding="utf-8"), result.returncode)
