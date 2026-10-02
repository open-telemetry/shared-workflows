from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Callable
from typing import Any


def subprocess_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def stop_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        if process.poll() is not None:
            return
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            creationflags=subprocess.CREATE_NO_WINDOW,
            capture_output=True,
            check=True,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # Descendants can outlive the group leader.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_monitored(
    command: list[str],
    lease_check: Callable[[], None],
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    lease_check()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        **subprocess_options(),
        **kwargs,
    )
    try:
        while True:
            lease_check()
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                lease_check()
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        stop_process_tree(process)
        process.communicate()
        raise
