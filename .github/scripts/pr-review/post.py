"""Publish the validated result to the PR."""

import json
import tempfile
from pathlib import Path

from common import check_snapshot, gh_json, progress, write_json


def publish_result(repo: str, pr: int, result_path: Path) -> None:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result["pr"] != pr or result["repository"] != repo:
        raise RuntimeError("Review result targets a different PR")
    review = result["review"]
    check_snapshot(repo, pr, result["base_sha"], review["commit_id"])
    if not review["comments"]:
        comment = gh_json(
            ["api", "-X", "POST", f"repos/{repo}/issues/{pr}/comments", "-f", "body=No findings."]
        )
        progress(f"Posted {comment['html_url']}")
        return

    with tempfile.TemporaryDirectory() as directory:
        payload = Path(directory) / "review.json"
        write_json(payload, {**review, "event": "COMMENT"})
        response = gh_json(
            ["api", f"repos/{repo}/pulls/{pr}/reviews", "--method", "POST", "--input", str(payload)]
        )
    progress(f"Published review {response['html_url']}")
