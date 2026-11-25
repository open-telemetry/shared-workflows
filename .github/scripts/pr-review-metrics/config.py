import json
import re
from datetime import date
from pathlib import Path

from github_data import DataError


OWNER = "open-telemetry"
CONFIG = Path(__file__).with_name("repositories.json")


def repository_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        raise DataError("Use a repository name without an owner, path, or URL")
    return value.lower()


def load_config(path=CONFIG):
    settings = json.loads(path.read_text(encoding="utf-8"))
    repos = settings["repositories"]
    if not repos or any(name != repository_name(name)
                        or entry.keys() != {"publish_instructions"}
                        or type(entry["publish_instructions"]) is not bool
                        for name, entry in repos.items()):
        raise DataError("Invalid PR review metrics opt-in configuration")
    return date.fromisoformat(settings["start"]), repos


def target_repository(value):
    name = repository_name(value)
    _, repos = load_config()
    if name not in repos:
        raise DataError(f"{OWNER}/{name} has not opted in to review metrics")
    return f"{OWNER}/{name}", repos[name]["publish_instructions"]
