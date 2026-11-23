"""Run Copilot against a public snapshot with read-only tools."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


STARTED = "[instruction-audit] Verified credential isolation"
ISOLATION_FAILURES = (
    "Docker socket is visible",
    "Actions credential is visible",
    "Copilot proxy token not isolated",
    "Host configuration is visible",
    "Expected checkout is not available",
    "Audit inputs are not read-only",
    "Copilot home is not writable",
    "Copilot CLI version check failed",
)
CLI_FAILURES = (
    (r"unrecognized (?:option|argument)|unexpected argument|unknown (?:option|argument)|Usage: copilot",
     "command-line argument error"),
    (r"not authenticated|authentication|HTTP 401|HTTP 403|unauthorized|forbidden", "authentication or permission error"),
    (r"model.{0,80}(?:unsupported|not available|not supported|not found|invalid)|invalid model",
     "model unavailable or unsupported"),
    (r"reasoning effort|reasoning.?effort", "reasoning effort rejected"),
    (r"rate limit|quota|HTTP 429", "rate or quota limit"),
    (r"permission denied|read.only file system|EACCES|EPERM|EROFS", "file permission error"),
    (r"cannot find module|not found|no such file|ENOENT|shared libraries", "missing runtime file"),
    (r"ECONN|ETIMEDOUT|network error|unable to connect|fetch failed", "connection error"),
    (r"syntax error|fatal error|uncaught exception|segmentation fault", "runtime error"),
)


def failure(returncode, diagnostics):
    for reason in ISOLATION_FAILURES:
        if f"Sandbox check failed: {reason}" in diagnostics.splitlines():
            return f"Copilot was not started: {reason}"
    if STARTED not in diagnostics.splitlines():
        return f"Copilot startup was not confirmed (firewall exit {returncode})"
    signature = next((label for pattern, label in CLI_FAILURES
                      if re.search(pattern, diagnostics, flags=re.IGNORECASE)), None)
    if not signature and '"type":"model.call_start"' in diagnostics:
        signature = "model call started"
    if not signature and '"type":"session.start"' in diagnostics:
        signature = "Copilot session started without a model call"
    if not signature and "[instruction-audit] Copilot binary executed" in diagnostics.splitlines():
        signature = "binary started but no Copilot session event was captured"
    detail = f": {signature}" if signature else ""
    return f"Copilot did not complete successfully (firewall exit {returncode}){detail}"


def response(events, returncode, diagnostics=""):
    if returncode:
        raise ValueError(failure(returncode, diagnostics))
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
    if error or not answer:
        raise ValueError("Copilot reported a session error or returned no answer")
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
    awf_root = Path(tempfile.mkdtemp(prefix="instruction-audit-awf-", dir=os.environ["RUNNER_TEMP"]))
    capture = awf_root / "output"
    result = subprocess.run([
        "/usr/bin/sudo", "--non-interactive", f"--preserve-env={','.join(env)}",
        str(Path(awf).resolve()), "--config", str(config), "--work-dir", str(awf_root / "work"),
        "--image-tag", os.environ["AWF_IMAGE_TAG"], "--container-workdir", str(checkout),
        "--mount", f"{cli_dir}:{cli_dir}:ro", "--mount", f"{bundle}:{bundle}:ro",
        "--env", "GITHUB_COPILOT_PROMPT_MODE_EXTENSIONS=false",
        "--capture-command-output", str(capture), "--proxy-logs-dir", str(awf_root / "proxy-logs"),
        "--agent-timeout", "18",
        "--log-level", "info", "--",
        "/bin/bash", str(bundle / "launch-copilot.sh"), str(checkout), str(bundle), str(config), copilot,
        "--model", "gpt-6.1-sol", "--reasoning-effort", "max", "--output-format", "json", "--no-ask-user",
        "--no-auto-update", "--no-custom-instructions", "--disable-builtin-mcps", "--allow-all-paths",
        "--available-tools", "view", "glob", "grep", "--allow-tool", "view", "glob", "grep",
    ], cwd=checkout, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1200,
       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = capture / "attempt-001.stdout.bin"
    stderr = capture / "attempt-001.stderr.bin"
    diagnostics = result.stderr.decode("utf-8", errors="replace")
    if stderr.is_file():
        with stderr.open("rb") as file:
            diagnostics += "\n" + file.read(64 * 1024).decode("utf-8", errors="replace")
    if result.returncode:
        if output.is_file():
            with output.open("rb") as file:
                diagnostics += "\n" + file.read(64 * 1024).decode("utf-8", errors="replace")
        raise ValueError(failure(result.returncode, diagnostics))
    if not output.is_file() or output.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Copilot output is missing or too large")
    return response(output.read_text(encoding="utf-8"), result.returncode, diagnostics)
