import base64
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode

import audit


CENTRAL = "open-telemetry/shared-workflows"
BOT = "opentelemetrybot"
CONFIG = Path(__file__).with_name("repositories.json")


def settings(name, path=CONFIG):
    audit.text(name, 100, audit.NAME)
    repositories = json.loads(path.read_text(encoding="utf-8"))["repositories"]
    for repository, entry in repositories.items():
        audit.text(repository, 100, audit.NAME)
        if (type(entry) is not dict or set(entry) - {"publish_audit_fixes", "max_open_audit_prs"}
                or type(entry.get("publish_audit_fixes", False)) is not bool
                or type(entry.get("max_open_audit_prs", 5)) is not int
                or entry.get("max_open_audit_prs", 5) < 1):
            raise ValueError("Invalid instruction audit enrollment")
    if name not in repositories:
        raise ValueError("Repository is not enrolled in instruction audits")
    return {"publish_audit_fixes": False, "max_open_audit_prs": 5, **repositories[name]}


def target():
    if os.environ["GITHUB_REPOSITORY"].lower() != CENTRAL:
        raise ValueError("Run central audits only from shared-workflows")
    name = os.environ["TARGET_REPOSITORY"]
    settings(name)
    return "open-telemetry/" + name


def state_branch(repo):
    return audit.STATE + "-" + repo.split("/")[1]


def read_state(repo):
    branch = state_branch(repo)
    ref = audit.api(CENTRAL, "/git/ref/heads/" + branch, missing=True)
    if ref is None:
        return None, {"anchor": None, "sha": None, "offset": 0}
    head = audit.text(ref["object"]["sha"], 40, audit.SHA)
    data = audit.api(CENTRAL, "/contents/cursor.json?ref=" + head)
    if data["type"] != "file" or data["encoding"] != "base64" or data["size"] > audit.LIMIT * 2:
        raise ValueError("Invalid central audit state")
    state = json.loads(base64.b64decode("".join(data["content"].split()), validate=True))
    audit.fields(state, "anchor", "sha", "offset")
    if state["anchor"] is not None:
        audit.validate_anchor(state["anchor"])
    audit.optional_sha(state["sha"])
    if (type(state["offset"]) is not int or not 0 <= state["offset"] <= 999999
            or (state["sha"] is None and state["offset"] != 0)):
        raise ValueError("Invalid central audit offset")
    return head, state


def write_state(repo, expected, state):
    branch = state_branch(repo)
    current, _ = read_state(repo)
    if current != expected:
        raise ValueError("Central audit state changed during this run")
    tree = audit.api(CENTRAL, "/git/trees", method="POST", body={"tree": [{
        "path": "cursor.json", "mode": "100644", "type": "blob",
        "content": json.dumps(state, ensure_ascii=False) + "\n",
    }]})
    commit = audit.api(CENTRAL, "/git/commits", method="POST", body={
        "message": "Save instruction audit for " + repo,
        "tree": audit.text(tree["sha"], 40, audit.SHA), "parents": [expected] if expected else [],
    })
    sha = audit.text(commit["sha"], 40, audit.SHA)
    if expected:
        audit.api(CENTRAL, "/git/refs/heads/" + branch, method="PATCH", body={"sha": sha, "force": False})
    else:
        audit.api(CENTRAL, "/git/refs", method="POST", body={"ref": "refs/heads/" + branch, "sha": sha})
    return sha


def prepare(repo):
    meta = audit.api(repo)
    if (meta["full_name"].lower() != repo or meta["visibility"] != "public"
            or meta["archived"] or meta["disabled"]):
        raise ValueError("Audit target must be an active public repository")
    head, state = read_state(repo)
    continuation = f"{state['sha']}:none:{state['offset']}" if state["offset"] else ""
    with tempfile.TemporaryDirectory(prefix="instruction-audit-") as directory:
        plan, prompt = audit.prepare(
            repo, Path(directory), publish_fixes=settings(repo.split("/")[1])["publish_audit_fixes"],
            continuation=continuation, pinned=state["sha"], cursor_record=(None, None, state["anchor"]),
        )
    if state["sha"] is None:
        state["sha"] = plan["result"]["sha"]
        head = write_state(repo, head, state)
    plan.update(state_head=head, state=state)
    audit.PLAN.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    if prompt is not None:
        audit.PROMPT.write_text(prompt, encoding="utf-8")
    audit.output("snapshot", plan["result"]["sha"])
    audit.output("repository", repo)
    audit.output("work", str(prompt is not None).lower())


def save(repo, result, plan, *, published=False):
    state = plan["state"]
    if state["sha"] != result["sha"] or repo != result["repo"]:
        raise ValueError("Audit outcome does not match the active pass")
    duplicate = published and not plan["resumable"]
    if result["status"] == "continue" or (duplicate and plan["next"]):
        state["offset"] += 1
    else:
        anchor = None if result["status"] == "complete" or duplicate else (
            result["rule"] if published else result["completed"] or state["anchor"]
        )
        state = {"anchor": anchor, "sha": None, "offset": 0}
    write_state(repo, plan["state_head"], state)
    audit.output("continue", str(result["status"] == "continue").lower())
    audit.summary("Central audit state saved.")


def fix_branch(result):
    key = json.dumps([result["sha"], result["rule"], result["first_line"], result["last_line"]],
                     sort_keys=True).encode()
    return audit.FIX_PREFIX + hashlib.sha256(key).hexdigest()[:32]


def pulls(repo, **params):
    query = "/pulls?" + urlencode({**params, "per_page": 100})
    for page in range(1, 21):
        items = audit.api(repo, query + f"&page={page}")
        yield from items
        if len(items) < 100:
            return
    raise ValueError("Could not inspect every audit PR")


def check_cap(repo, maximum):
    count = sum(
        1 for pr in pulls(repo, state="open")
        if pr["head"]["ref"].startswith(audit.FIX_PREFIX)
        and (pr["head"].get("repo") or {}).get("full_name", "").lower() in {
            repo, f"{BOT}/{repo.split('/')[1]}"
        }
    )
    if count >= maximum:
        raise ValueError(f"Open audit PR limit reached ({maximum}). No PR opened.")


def ensure_fork(repo):
    # Reuse the instruction generator's identity and fork-parent checks.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "copilot-review-instructions"))
    from github_data import GitHub
    from publish_instructions import push_fork

    push_fork(repo, GitHub(max_rate_wait=10, max_requests=30))
    return f"{BOT}/{repo.split('/')[1]}"


def publish(repo, result):
    config = settings(repo.split("/")[1])
    if not config["publish_audit_fixes"] or result["fix"] is None:
        raise ValueError("This audit has no authorized publishable fix")
    fork = ensure_fork(repo)
    branch = fix_branch(result)
    marker = f"<!-- shared-workflows:instruction-audit {branch}"
    body = audit.pull_request_body(result)
    if len(body) + len(f"\n\n{marker} {'0' * 40} -->") > 65_536:
        raise ValueError("The audit description and ownership marker exceed GitHub's PR limit")
    existing = list(pulls(repo, state="all", head=f"{BOT}:{branch}"))
    if existing:
        if len(existing) != 1:
            raise ValueError("Multiple PRs match the pending audit publication")
        pull = existing[0]
        ref = audit.api(fork, "/git/ref/heads/" + branch, missing=True)
        ownership = re.search(re.escape(marker) + r" ([0-9a-f]{40}) -->", pull.get("body") or "")
        if (pull["user"]["login"].lower() != BOT or not ownership or ownership[1] != pull["head"]["sha"]
                or (pull["head"].get("repo") or {}).get("full_name", "").lower() != fork
                or pull["base"]["ref"] != result["base"]
                or (ref and ref["object"]["sha"] != pull["head"]["sha"])):
            raise ValueError("Existing audit PR is unmanaged or changed")
        audit.summary("Audit PR already published: " + pull["html_url"])
        audit.output("handled", "true")
        return
    check_cap(repo, config["max_open_audit_prs"])
    meta = audit.api(repo)
    if (meta["visibility"] != "public" or meta["archived"] or meta["disabled"]
            or meta["default_branch"] != result["base"]):
        raise ValueError("Target repository is no longer an active public audit target")
    # Fork Git objects are shared with their parent, including the pinned base tree.
    ref = audit.api(fork, "/git/ref/heads/" + branch, missing=True)
    if ref is None:
        tree = audit.api(fork, "/git/trees", method="POST", body={
            "base_tree": result["tree"], "tree": result["fix"],
        })
        commit = audit.api(fork, "/git/commits", method="POST", body={
            "message": audit.fix_commit_message(result),
            "tree": audit.text(tree["sha"], 40, audit.SHA), "parents": [result["sha"]],
        })
    else:
        raise ValueError("Unconfirmed audit branch exists; inspect the bot fork before retrying")
    commit_sha = audit.text(commit["sha"], 40, audit.SHA)
    # Check before publishing the audit ref so a cap failure cannot strand the branch.
    check_cap(repo, config["max_open_audit_prs"])
    audit.api(fork, "/git/refs", method="POST", body={
        "ref": "refs/heads/" + branch, "sha": commit_sha,
    })
    pull = audit.api(repo, "/pulls", method="POST", body={
        "title": f"Instruction audit: {result['title']}",
        "body": body + f"\n\n{marker} {commit_sha} -->", "base": result["base"],
        "head": f"{BOT}:{branch}", "draft": False, "maintainer_can_modify": False,
    })
    if pull["draft"] is not False:
        raise ValueError("GitHub did not confirm a ready-for-review audit PR")
    audit.output("handled", "true")
    audit.summary("Audit PR: " + pull["html_url"])


def main(command):
    repo = target()
    # The result validator always checks the enrolled target, not the orchestration repository.
    os.environ["AUDIT_ORCHESTRATOR"] = CENTRAL
    os.environ["GITHUB_REPOSITORY"] = repo
    if command == "prepare":
        prepare(repo)
    elif command == "assess":
        audit.main("assess")
    elif command == "publish":
        publish(repo, audit.load_result())
    elif command == "save":
        save(repo, audit.load_result(), json.loads(audit.PLAN.read_bytes()),
             published=os.environ["AUDIT_PUBLISHED"] == "true")
    else:
        raise ValueError("Unknown central audit command")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except (ValueError, KeyError) as error:
        raise SystemExit(str(error) if type(error) is ValueError else "Malformed audit response") from None
