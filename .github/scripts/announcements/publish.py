"""Validate, preview, and deliver announcements without automatic write retries."""

import html
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


CHANNELS = {"linkedin", "bluesky", "mastodon", "slack", "groupsio"}
BUFFER_CHANNELS = {"linkedin", "bluesky", "mastodon"}
DRAFT_CHANNELS = BUFFER_CHANNELS | {"groupsio"}
MODES = {"check", "draft", "publish"}
# Limits for the rendered post. Counting code points is conservative for Bluesky
# (which counts graphemes) and Mastodon (which counts every URL as 23 characters).
LIMITS = {"bluesky": 300, "mastodon": 500, "linkedin": 3000, "slack": 40000}
WAIT_INTERVAL = 30
MAX_WAIT_MINUTES = 120
REPORT_FIELDS = ("channel", "target", "action", "status", "remote_id", "remote_status", "error")
SECRETS = {
    "linkedin": ("BUFFER_API_KEY",),
    "bluesky": ("BUFFER_API_KEY",),
    "mastodon": ("BUFFER_API_KEY",),
    "slack": ("SLACK_WEBHOOK_URL",),
    "groupsio": ("GROUPSIO_API_KEY",),
}


class DeliveryError(Exception):
    """An explicitly reported validation or delivery failure."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward credentials or turn an API write into a redirected GET.
        return None


def request(url, payload, headers=None, *, form=False, empty_ok=False, raw=False):
    data = urlencode(payload).encode() if form else json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded" if form else "application/json",
        **(headers or {}),
    }
    try:
        with build_opener(NoRedirects()).open(
            Request(url, data=data, headers=headers, method="POST"), timeout=30
        ) as response:
            body = response.read()
    except HTTPError as exc:
        detail = ""
        if raw:
            # Slack webhooks report errors as short codes.
            try:
                detail = f" ({api_error_code(exc.read().decode().strip())})"
            except (OSError, UnicodeDecodeError, AttributeError):
                pass
        uncertain = "; delivery may have occurred" if exc.code >= 500 else ""
        raise DeliveryError(f"API returned HTTP {exc.code}{detail}{uncertain}; not retried") from exc
    except (URLError, TimeoutError, OSError, HTTPException) as exc:
        raise DeliveryError(
            "API connection failed; delivery may have occurred; not retried"
        ) from exc
    if raw:
        return body.decode("utf-8", errors="replace")
    if not body and empty_ok:
        return None
    try:
        result = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DeliveryError("API returned an invalid JSON response; not retried") from exc
    if not isinstance(result, dict):
        raise DeliveryError("API returned an unexpected response object")
    return result


def text_field(value, name, *, limit=40000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DeliveryError(f"{name} must be a nonempty string of at most {limit} characters")
    if any(
        (ord(char) < 32 and char not in "\n\t") or 0xD800 <= ord(char) <= 0xDFFF
        for char in value
    ):
        raise DeliveryError(f"{name} contains control characters")
    return value


def slack_escape(text):
    # Slack treats <...> as control sequences such as <!channel>, even with parse=none.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(channel, title, text, url):
    """Return the message body; Groups.io sends the title separately as the subject."""
    if channel == "slack":
        return f"{title}\n\n{text}\n\n{url}"
    return f"{text}\n\n{url}"


def parse_json(value, name):
    try:
        return json.loads(value)
    except (ValueError, TypeError) as exc:
        raise DeliveryError(f"{name} must be valid JSON") from exc


def api_error_code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_:-]{1,80}", value) else "unknown"


def build_plan(announcement, channels, targets, mode):
    if mode not in MODES:
        raise DeliveryError("mode must be check, draft, or publish")
    if not isinstance(announcement, dict) or set(announcement) - {
        "title", "text", "url", "overrides"
    }:
        raise DeliveryError("announcement must contain only title, text, url, and overrides")
    title = text_field(announcement.get("title"), "title", limit=300)
    text = text_field(announcement.get("text"), "text")
    url = text_field(announcement.get("url"), "url", limit=2048)
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise DeliveryError("url must be a valid absolute HTTPS URL") from exc
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username
        or parsed.password or any(char.isspace() for char in url)
    ):
        raise DeliveryError("url must be an absolute HTTPS URL without credentials or whitespace")
    if (
        not isinstance(channels, list) or not channels
        or any(not isinstance(channel, str) or channel not in CHANNELS for channel in channels)
        or len(set(channels)) != len(channels)
    ):
        raise DeliveryError("channels must be a nonempty JSON array of unique supported channels")
    if not isinstance(targets, dict) or set(targets) - CHANNELS:
        raise DeliveryError("targets must be an object keyed by supported channel names")
    overrides = announcement.get("overrides", {})
    if not isinstance(overrides, dict) or set(overrides) - CHANNELS:
        raise DeliveryError("overrides must be an object keyed by supported channel names")
    for channel, override in overrides.items():
        if not isinstance(override, dict) or set(override) - {"title", "text"}:
            raise DeliveryError(f"{channel} override may contain only title and text")
        for key, value in override.items():
            text_field(value, f"{channel}.{key}", limit=300 if key == "title" else 40000)
    # Validate configured targets even when not selected, to catch configuration typos.
    for channel, destinations in targets.items():
        if not isinstance(destinations, list) or not destinations:
            raise DeliveryError(f"{channel} targets must be a nonempty array")
        for destination in destinations:
            if channel == "groupsio":
                valid = type(destination) is int and destination > 0
            else:
                pattern = r"[CG][A-Z0-9]+" if channel == "slack" else r"[A-Za-z0-9_-]+"
                valid = isinstance(destination, str) and re.fullmatch(pattern, destination)
            if not valid:
                raise DeliveryError(f"Invalid {channel} destination")
        normalized = [str(value).lower() for value in destinations]
        if len(set(normalized)) != len(normalized):
            raise DeliveryError(f"Duplicate {channel} destination")
    plan = []
    buffer_ids = set()
    for channel in channels:
        if channel not in targets and mode != "check":
            raise DeliveryError(f"No targets configured for {channel}")
        override = overrides.get(channel, {})
        channel_title = override.get("title", title)
        body = render(channel, channel_title, override.get("text", text), url)
        length = len(slack_escape(body)) if channel == "slack" else len(body)
        if length > LIMITS.get(channel, 40000):
            raise DeliveryError(
                f"{channel} message is {length} characters including the URL; "
                f"the limit is {LIMITS.get(channel, 40000)}"
            )
        # Content checks (for example on pull requests) need no destinations.
        for destination in targets.get(channel, [None]):
            if channel in BUFFER_CHANNELS and destination is not None:
                if destination in buffer_ids:
                    raise DeliveryError("A Buffer channel ID cannot target multiple services")
                buffer_ids.add(destination)
            action = (
                "preview" if mode == "check" or (mode == "draft" and channel not in DRAFT_CHANNELS)
                else mode
            )
            plan.append({
                "channel": channel, "target": destination, "action": action,
                "title": channel_title, "text": body, "url": url,
                "status": "pending",
            })
    if len(plan) > 20:
        raise DeliveryError("At most 20 destinations may be selected per run")
    return plan


class Publisher:
    def __init__(self, credentials, *, groupsio_host="lists.cncf.io"):
        if groupsio_host not in {"lists.cncf.io", "groups.io"}:
            raise DeliveryError("groupsio_host must be lists.cncf.io or groups.io")
        self.credentials = credentials
        self.groupsio_base = f"https://{groupsio_host}/api/v1"

    def auth(self, secret):
        return {"Authorization": f"Bearer {self.credentials[secret]}"}

    def buffer(self, query, variables):
        result = request(
            "https://api.buffer.com", {"query": query, "variables": variables},
            self.auth("BUFFER_API_KEY"),
        )
        if result.get("errors") or not isinstance(result.get("data"), dict):
            raise DeliveryError("Buffer GraphQL request failed")
        return result["data"]

    def preflight(self, plan):
        active = [item for item in plan if item["action"] != "preview"]
        missing = sorted({
            secret for item in active for secret in SECRETS[item["channel"]]
            if not self.credentials.get(secret)
        })
        if missing:
            raise DeliveryError(f"Missing secrets: {', '.join(missing)}")
        required = {secret for item in active for secret in SECRETS[item["channel"]]}
        for secret in required:
            value = self.credentials[secret]
            if not isinstance(value, str) or any(not 33 <= ord(char) <= 126 for char in value):
                raise DeliveryError(f"{secret} must contain only printable ASCII without whitespace")
        if any(item["channel"] == "slack" for item in active) and not re.fullmatch(
            r"https://hooks\.slack\.com/triggers/[A-Za-z0-9/_-]+", self.credentials["SLACK_WEBHOOK_URL"]
        ):
            raise DeliveryError("SLACK_WEBHOOK_URL must be a Slack workflow webhook (https://hooks.slack.com/triggers/...)")
        # Check every Buffer channel before the first write: a mislabeled ID must
        # not silently route a LinkedIn announcement to a different network.
        for item in active:
            if item["channel"] not in BUFFER_CHANNELS:
                continue
            data = self.buffer(
                "query($id: ChannelId!) { channel(input: {id: $id}) "
                "{ id service isDisconnected isLocked } }",
                {"id": item["target"]},
            )
            channel = data.get("channel")
            if (
                not isinstance(channel, dict)
                or channel.get("id") != item["target"]
                or channel.get("service") != item["channel"]
                or channel.get("isDisconnected") is not False
                or channel.get("isLocked") is not False
            ):
                raise DeliveryError("Buffer channel is unavailable or does not match its selected service")

    def deliver(self, item):
        channel = item["channel"]
        if channel in BUFFER_CHANNELS:
            data = self.buffer(
                "mutation($input: CreatePostInput!) { createPost(input: $input) "
                "{ ... on PostActionSuccess { post { id status } } "
                "... on MutationError { message } } }",
                {"input": {
                    "channelId": item["target"], "text": item["text"],
                    "schedulingType": "automatic",
                    "mode": "addToQueue" if item["action"] == "draft" else "shareNow",
                    "saveToDraft": item["action"] == "draft",
                }},
            )
            result = data.get("createPost")
            if not isinstance(result, dict) or result.get("message"):
                raise DeliveryError("Buffer rejected the post; check content limits and account permissions")
            post = result.get("post")
            if not isinstance(post, dict) or not post.get("id"):
                raise DeliveryError("Buffer did not return a post ID")
            item["remote_id"] = post["id"]
            item["remote_status"] = post.get("status")
            allowed = {"draft"} if item["action"] == "draft" else {"sending", "sent", "scheduled"}
            if post.get("status") not in allowed:
                raise DeliveryError("Buffer returned an unexpected post status")
        elif channel == "slack":
            # Workflow Builder webhooks accept only the trigger's declared variables.
            result = request(
                self.credentials["SLACK_WEBHOOK_URL"],
                {"channel": item["target"], "text": slack_escape(item["text"])},
                raw=True,
            ).strip()
            try:
                parsed = json.loads(result)
            except ValueError:
                parsed = None
            if result != "ok" and not (isinstance(parsed, dict) and parsed.get("ok") is True):
                error = parsed.get("error") if isinstance(parsed, dict) else result
                raise DeliveryError(
                    f"Slack rejected the message ({api_error_code(error)}); "
                    "check the workflow's channel and text variables"
                )
        elif channel == "groupsio":
            headers = self.auth("GROUPSIO_API_KEY")
            draft = request(
                f"{self.groupsio_base}/newdraft",
                {"group_id": item["target"], "draft_type": "draft_type_post"},
                headers, form=True,
            )
            if draft.get("object") != "draft" or type(draft.get("id")) is not int:
                raise DeliveryError("Groups.io did not return a draft ID")
            if draft["id"] <= 0:
                raise DeliveryError("Groups.io returned an invalid draft ID")
            item["remote_id"] = draft["id"]
            item["remote_status"] = "empty-draft"
            updated = request(
                f"{self.groupsio_base}/updatedraft",
                {"draft_id": draft["id"], "subject": item["title"],
                 "body": "<p>" + html.escape(item["text"]).replace("\n", "<br>\n") + "</p>"},
                headers, form=True,
            )
            if updated.get("object") != "draft" or updated.get("id") != draft["id"]:
                raise DeliveryError("Groups.io did not confirm the draft update")
            item["remote_status"] = "draft"
            if item["action"] == "publish":
                result = request(
                    f"{self.groupsio_base}/postdraft", {"draft_id": draft["id"]},
                    headers, form=True, empty_ok=True,
                )
                if result is not None:
                    raise DeliveryError("Groups.io returned an unexpected postdraft response")
                item["remote_status"] = "submitted"
        else:
            raise DeliveryError("Unsupported delivery channel")

    def run(self, plan, on_progress=None):
        self.preflight(plan)
        for item in plan:
            if item["action"] == "preview":
                item["status"] = "preview"
                if on_progress:
                    on_progress()
                continue
            try:
                self.deliver(item)
            except DeliveryError as exc:
                item["status"] = "failed"
                item["error"] = str(exc)
            except Exception as exc:  # A bug must not hide that a write may have happened.
                item["status"] = "failed"
                item["error"] = (
                    f"Unexpected {type(exc).__name__}; delivery may have occurred; not retried"
                )
            else:
                item["status"] = "drafted" if item["action"] == "draft" else "submitted"
            if on_progress:
                on_progress()
        return not any(item["status"] == "failed" for item in plan)


def url_status(url):
    try:
        with build_opener(NoRedirects()).open(
            Request(url, headers={"User-Agent": "opentelemetry-announcements"}), timeout=30
        ) as response:
            return response.status
    except HTTPError as exc:
        return exc.code
    except (URLError, TimeoutError, OSError, HTTPException):
        return None


def wait_until_live(url, minutes, *, status=url_status, sleep=time.sleep, clock=time.monotonic):
    """Poll until the URL answers 200 without a redirect, or fail after the deadline."""
    deadline = clock() + minutes * 60
    while True:
        code = status(url)
        if code == 200:
            print(f"{url} is live.", file=sys.stderr)
            return
        remaining = deadline - clock()
        if remaining <= 0:
            raise DeliveryError(
                f"{url} was not live after {minutes} minutes (last response: {code or 'none'})"
            )
        print(f"{url} returned {code or 'no response'}; checking again in "
              f"{WAIT_INTERVAL} seconds.", file=sys.stderr)
        sleep(min(WAIT_INTERVAL, remaining))


def wait_minutes(value):
    if not re.fullmatch(r"[0-9]{1,3}", value or "") or int(value) > MAX_WAIT_MINUTES:
        raise DeliveryError(f"wait_for_url_minutes must be an integer from 0 to {MAX_WAIT_MINUTES}")
    return int(value)


def environment_plan():
    return build_plan(
        parse_json(os.environ.get("ANNOUNCEMENT", ""), "announcement"),
        parse_json(os.environ.get("ANNOUNCEMENT_CHANNELS", ""), "channels"),
        parse_json(os.environ.get("ANNOUNCEMENT_TARGETS", ""), "targets"),
        os.environ.get("ANNOUNCEMENT_MODE", "check"),
    )


def rendered_summary(plan):
    rendered = {}
    for item in plan:
        rendered.setdefault(item["channel"], item)
    lines = ["", "### Rendered content", ""] if rendered else []
    for channel, item in rendered.items():
        lines.append(
            f"<details><summary>{channel}: {html.escape(item['title'])}</summary>"
            f"<pre>{html.escape(item['text'])}</pre></details>"
        )
    return lines


def write_summary(lines):
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")


def check():
    """Validate and render before approval, then optionally wait until the URL is live."""
    summary = ["## Announcement check", ""]
    try:
        minutes = wait_minutes(os.environ.get("ANNOUNCEMENT_WAIT_MINUTES", "0"))
        plan = environment_plan()
        write_summary([*summary, *rendered_summary(plan)])
        if minutes:
            wait_until_live(plan[0]["url"], minutes)
    except DeliveryError as exc:
        print(f"::error::{str(exc).replace('%', '%25')}")
        write_summary([*summary, str(exc)])
        return 1
    print("Announcement check passed.", file=sys.stderr)
    return 0


def deliver():
    output = Path(os.environ["ANNOUNCEMENT_REPORT_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    report = {"mode": os.environ.get("ANNOUNCEMENT_MODE", "check"), "deliveries": []}
    success = False

    def save_report():
        # The report is an audit record; rendered content is reproducible from the inputs.
        saved = {**report, "deliveries": [
            {key: value for key, value in item.items() if key in REPORT_FIELDS}
            for item in report["deliveries"]
        ]}
        temporary = output / "report.json.tmp"
        temporary.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output / "report.json")

    try:
        report["deliveries"] = environment_plan()
        publisher = Publisher(
            os.environ, groupsio_host=os.environ.get("GROUPSIO_HOST", "lists.cncf.io"),
        )
        save_report()
        success = publisher.run(report["deliveries"], on_progress=save_report)
    except DeliveryError as exc:
        report["error"] = str(exc)
    report["success"] = success
    save_report()
    summary = ["## Announcement delivery", "", f"Mode: `{report['mode']}`", ""]
    for status in ("preview", "drafted", "submitted", "failed", "pending"):
        count = sum(item["status"] == status for item in report["deliveries"])
        summary.append(f"- {status}: {count}")
    if "error" in report:
        summary.extend(["", report["error"]])
    summary.extend(rendered_summary(report["deliveries"]))
    summary.extend(["", "See the announcement report artifact for destinations and delivery IDs."])
    write_summary(summary)
    print("Announcement delivery completed." if success else "Announcement delivery failed; inspect report.json.", file=sys.stderr)
    return 0 if success else 1


def main():
    return check() if os.environ.get("ANNOUNCEMENT_STAGE") == "check" else deliver()


if __name__ == "__main__":
    sys.exit(main())
