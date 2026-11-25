import argparse
import os
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from archive import Archive
from collect_hosted import open_snapshot, remote_tip
from config import load_config, target_repository
from github_data import DataError, GitHub
from scorecard import COMMAND_ERRORS, main as scorecard_main


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    try:
        repo, publish = target_repository(args.repository)
        info = GitHub().public(repo)
        if info["archived"] or info["disabled"]:
            raise DataError(f"{repo} is archived or disabled")
        start, _ = load_config()
        through = datetime.now(timezone.utc).date()
        expected = remote_tip()
        if not expected:
            raise DataError("Run the review metrics collector before generating instructions")
        archive_path = args.work / "archive.sqlite3"
        with closing(Archive(archive_path)) as archive:
            open_snapshot(archive, expected, None)
        result = scorecard_main([
            "draft-instructions", "--archive", str(archive_path), "--repo", repo,
            "--window", str(start), str(through), "--min-examples", "2",
            "--model", "gpt-6.2-sol", "--reasoning-effort", "max",
            "--editor-model", "claude-opus-5.5", "--editor-reasoning-effort", "high",
            "--output-dir", str(args.artifact),
        ])
        if result:
            return result
        patch = args.artifact / "copilot-instructions.patch"
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            print(f"publish={str(publish and patch.stat().st_size > 0).lower()}", file=output)
        return 0
    except COMMAND_ERRORS as error:
        parser.exit(1, f"Instruction generation stopped: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
