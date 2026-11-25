import argparse
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep

from config import target_repository
from github_data import DataError, GitHub, GitHubError, run_command
from git_evidence import object_id
from review_instructions import git, is_instruction


BOT = "opentelemetrybot"
BRANCH = "otelbot/copilot-review-instructions"
OWNERSHIP = re.compile(r"(?m)^<!-- shared-workflows:copilot-review-instructions ([0-9a-f]{40}) -->$")


def publishing_plan(github, repo, base):
    expected_fork = f"{BOT}/{repo.split('/')[1]}"
    managed = None
    for pull in github.rest_pages(f"repos/{repo}/pulls", state="open", base=base):
        head = pull["head"]
        if head["ref"] == BRANCH and (head["repo"] or {}).get("full_name", "").lower() == expected_fork:
            marker = OWNERSHIP.search(pull.get("body") or "")
            if not marker or marker[1] != head["sha"] or pull["user"]["login"].lower() != BOT:
                raise DataError(f"Not overwriting an unmanaged or manually changed bot PR: {pull['html_url']}")
            if managed is not None:
                raise DataError("Multiple managed PRs found for the bot branch")
            managed = pull
            continue
        if any(is_instruction(file["filename"])
               or is_instruction(file.get("previous_filename") or "")
               for file in github.rest_pages(f"repos/{repo}/pulls/{pull['number']}/files")):
            raise DataError(f"Another open PR changes Copilot instructions: {pull['html_url']}")

    if managed is not None:
        return managed, managed["head"]["sha"]
    for closed in github.rest_pages(f"repos/{repo}/pulls", state="closed", base=base,
                                    head=f"{BOT}:{BRANCH}", sort="updated", direction="desc"):
        marker = OWNERSHIP.search(closed.get("body") or "")
        if marker and closed["user"]["login"].lower() == BOT:
            if not github.get(f"repos/{repo}/pulls/{closed['number']}")["merged"]:
                raise DataError(f"Not recreating a closed instruction PR: {closed['html_url']}")
            return None, marker[1]
    return None, None


def push_fork(repo, github):
    if github.get("user")["login"].lower() != BOT:
        raise DataError(f"The publish credential must authenticate as {BOT}")
    fork = f"{BOT}/{repo.split('/')[1]}"
    path = f"repos/{fork}"
    try:
        info = github.get(path)
    except GitHubError as error:
        if "HTTP 404" not in str(error):
            raise
        github.write("POST", f"repos/{repo}/forks", {"default_branch_only": True})
        for attempt in range(6):
            try:
                info = github.get(path)
                break
            except GitHubError as pending:
                if "HTTP 404" not in str(pending) or attempt == 5:
                    raise
                sleep(5)
    if not info["fork"] or info.get("parent", {}).get("full_name", "").lower() != repo:
        raise DataError(f"{fork} is not a fork of {repo}")
    return f"https://github.com/{fork}.git"


def fork_head(url):
    result = run_command(["git", "ls-remote", "--exit-code", url, f"refs/heads/{BRANCH}"])
    if result.returncode == 2:
        return None
    head = result.stdout.partition("\t")[0]
    if result.returncode or not object_id(head):
        raise DataError(f"Could not resolve bot branch: {result.stderr.strip()}")
    return head


def pr_body(commit):
    return (
        "Replace the Copilot review instructions with rules drawn from current repository guides "
        "and addressed human reviews. Include the shared authoring guide for instruction files.\n\n"
        f"<!-- shared-workflows:copilot-review-instructions {commit} -->"
    )


def apply_draft(repo, base, artifact, revision, upstream, previous, directory):
    path = directory / "draft.git"
    git("init", "--bare", "--quiet", str(path))
    git("fetch", "--quiet", "--no-tags", "--depth=1", f"https://github.com/{repo}.git",
        f"refs/heads/{base}:refs/heads/upstream", git_dir=path)
    actual = git("rev-parse", "refs/heads/upstream", git_dir=path).strip()
    if actual != revision:
        raise DataError("The target default branch advanced during generation; dispatch again")
    git("read-tree", actual, git_dir=path)
    git("apply", "--cached", "--check", "-", git_dir=path, binary=True, input=artifact)
    git("apply", "--cached", "-", git_dir=path, binary=True, input=artifact)
    tree = git("write-tree", git_dir=path).strip()
    if previous:
        git("fetch", "--quiet", "--no-tags", "--depth=1", upstream,
            f"refs/heads/{BRANCH}:refs/heads/previous", git_dir=path)
        if git("rev-parse", "refs/heads/previous", git_dir=path).strip() != previous:
            raise DataError("The bot branch changed during generation")
        if git("rev-parse", f"{previous}^{{tree}}", git_dir=path).strip() == tree:
            return path, None
    parents = ["-p", previous, "-p", actual] if previous else ["-p", actual]
    commit = git("-c", f"user.name={BOT}", "-c", f"user.email={BOT}@users.noreply.github.com",
                 "commit-tree", tree, *parents, "-m", "Generate Copilot review instructions",
                 "-m", "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>",
                 git_dir=path).strip()
    return path, commit


def request_review(github, repo, number, commit):
    version = run_command(["gh", "--version"])
    match = re.search(r"^gh version (\d+)\.(\d+)\.", version.stdout)
    if not match:
        raise DataError("Could not identify the GitHub CLI version")
    if tuple(map(int, match.groups())) >= (2, 88):
        requested = run_command(["gh", "pr", "edit", str(number), "--repo", repo, "--add-reviewer", "@copilot"])
        if requested.returncode:
            raise DataError(f"Copilot review request failed: {requested.stderr.strip()}")
    else:
        github.write("POST", f"repos/{repo}/pulls/{number}/requested_reviewers",
                     {"reviewers": ["copilot-pull-request-reviewer[bot]"]})
    copilot = {"copilot-pull-request-reviewer", "copilot-pull-request-reviewer[bot]", "Copilot"}
    result = github.get(f"repos/{repo}/pulls/{number}/requested_reviewers")
    if any(user["login"] in copilot for user in result["users"]):
        return
    if any(review["commit_id"] == commit and (review.get("user") or {}).get("login") in copilot
           for review in github.rest_pages(f"repos/{repo}/pulls/{number}/reviews")):
        return
    raise DataError("GitHub did not confirm the Copilot review request")


def publish(name, directory, *, dry_run):
    repo, enabled = target_repository(name)
    github = GitHub(max_rate_wait=10, max_requests=250)
    info = github.public(repo)
    if info["archived"] or info["disabled"]:
        raise DataError(f"{repo} is archived or disabled")
    base = info["default_branch"]
    patch = (directory / "copilot-instructions.patch").read_bytes()
    data = json.loads((directory / "copilot-instructions.evidence.json").read_text(encoding="utf-8"))
    if data["repo"] != repo or not object_id(data["revision"]):
        raise DataError("The draft does not match the dispatch repository")
    if not patch:
        print("No change to Copilot instructions. No PR needed.")
        return
    if dry_run:
        try:
            managed, _ = publishing_plan(github, repo, base)
        except DataError as error:
            print(f"Dry run: publishing would stop. {error}")
        else:
            action = f"update {managed['html_url']}" if managed else "open a draft PR from the bot's fork"
            print(f"Dry run: would {action}. No repository changes made.")
        return
    if not enabled:
        raise DataError(f"{repo} has not opted in to bot PRs")
    managed, last = publishing_plan(github, repo, base)
    if github.get(f"repos/{repo}/git/ref/heads/{base}")["object"]["sha"] != data["revision"]:
        raise DataError("The target default branch advanced during generation; dispatch again")
    url = push_fork(repo, github)
    previous = fork_head(url)
    if (managed and previous != last) or (previous and previous != last):
        raise DataError("The bot branch is missing, unmanaged, or changed since the last generated commit")
    with TemporaryDirectory() as work:
        path, commit = apply_draft(repo, base, patch, data["revision"], url,
                                   previous if managed else None, Path(work))
        if commit is None:
            print("The bot branch already contains the generated instructions.")
            return
        # The lease also protects branch creation against a concurrent push.
        git("-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
            "push", f"--force-with-lease=refs/heads/{BRANCH}:{previous or ''}",
            url, f"{commit}:refs/heads/{BRANCH}", git_dir=path)
    if managed:
        path = f"repos/{repo}/pulls/{managed['number']}"
        live = github.get(path)
        body = live.get("body") or ""
        marker = OWNERSHIP.search(body)
        if live["state"] != "open" or not marker or marker[1] != last:
            raise DataError(f"The managed PR changed while publishing: {managed['html_url']}")
        updated = body[:marker.start(1)] + commit + body[marker.end(1):]
        pull = github.write("PATCH", path, {"body": updated})
    else:
        pull = github.write("POST", f"repos/{repo}/pulls", {
            "title": "Generate Copilot review instructions", "body": pr_body(commit),
            "head": f"{BOT}:{BRANCH}", "base": base, "draft": True,
        })
    print(pull["html_url"], flush=True)
    if fork_head(url) != commit:
        raise DataError("The generated commit could not be confirmed on the bot branch")
    try:
        request_review(github, repo, pull["number"], commit)
    except DataError as error:
        print(f"::warning::{error}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        publish(args.repository, args.artifact, dry_run=args.dry_run)
    except (DataError, OSError, ValueError) as error:
        parser.exit(1, f"Instruction publishing stopped: {error}\n")


if __name__ == "__main__":
    main()
