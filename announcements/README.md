# Announcements

Reusable workflow for distributing OpenTelemetry news and blog announcements.
The caller supplies prepared content, chooses services, and maps each service to
one or more destinations. LinkedIn, Bluesky, and Mastodon are delivered through
Buffer, and Slack through a Slack Workflow Builder webhook. CNCF mailing lists
use the Groups.io API directly.

## Modes

| Mode | Buffer | Groups.io | Slack |
| ---- | ------ | --------- | ----- |
| `check` (default) | Preview | Preview | Preview |
| `draft` | Create native drafts | Create native drafts | Preview, **no sending** |
| `publish` | Request immediate publishing | Post draft to the list | Send message |

Every run starts with a `check` job that has no environment and no secrets.
It validates the content and configuration, renders each channel's message to
the job summary, and, if `wait_for_url_minutes` is set, waits until the
announcement URL returns 200. Only then does the `deliver` job request
environment approval. In `check` mode the run stops after this job, so it
suits pull requests; targets may be omitted (`{}`).

`check` makes **no API requests** and needs no credentials, so it cannot
verify account access, remote IDs, or service availability. Previews appear in
the job summary only.
Draft runs require credentials only for Buffer and Groups.io destinations.
Draft creation is a remote write; repeating it creates additional drafts.
Slack previews are not native drafts and cannot later be published from
Slack.

### Verification status

Each delivery path is covered by unit tests with mocked requests. Against the
live services, only some have been exercised so far:

| Path | Live status |
| ---- | ----------- |
| Groups.io drafts | Verified on groups.io |
| Groups.io `publish` (`/postdraft`) | Not yet verified |
| Buffer drafts and `publish` | Not yet verified |
| Slack | Webhook accepts the payload; delivery to a channel not yet confirmed |

Treat unverified paths as experimental and test them with `draft` or test
destinations before relying on them.

## Calling the workflow

```yaml
name: Announce a blog post
on:
  workflow_dispatch:
    inputs:
      mode:
        type: choice
        options: [check, draft, publish]
        default: check

permissions: {}

jobs:
  announce:
    uses: open-telemetry/shared-workflows/.github/workflows/announcements.yml@<commit-sha>
    with:
      announcement: >-
        {"title":"New OpenTelemetry blog post",
         "text":"Learn about the latest OpenTelemetry improvements.",
         "url":"https://opentelemetry.io/blog/2026/example/"}
      channels: '["linkedin","bluesky","mastodon","slack","groupsio"]'
      targets: ${{ vars.ANNOUNCEMENT_TARGETS }}
      mode: ${{ inputs.mode }}
      environment: announcements
    secrets:
      BUFFER_API_KEY: ${{ secrets.BUFFER_API_KEY }}
      SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
      GROUPSIO_API_KEY: ${{ secrets.GROUPSIO_API_KEY }}
```

Replace `<commit-sha>` with a reviewed shared-workflows commit. The self-repository
action (`$/...`) loads delivery code from that exact workflow commit; no consumer
checkout or floating code ref is used by the reusable workflow.

Configure an `announcements` environment in the **calling repository**, with
required reviewers and deployment branch restrictions before enabling remote
writes. Environment secrets can supply these credentials instead of passing
repository secrets explicitly; same-named environment secrets take precedence.
Do not call this workflow from untrusted PR code or `pull_request_target` jobs
that check out a PR head. Only announce reviewed content from a trusted branch,
and restrict which workflows/repositories can access the publishing credentials.

### Visibility

Only the credentials are confidential. In a public calling repository, anyone
can read the run's logs, job summary, and report artifact. The logs show the
step inputs, including the announcement content, selected channels, and
targets. The report adds delivery status and remote IDs. Destination and
remote IDs cannot be used without the corresponding credentials.

**Do not use this workflow for embargoed or confidential content**, such as a
security advisory prepared ahead of its release, from a public repository.
Removing the report would not help, because the content is already in the
logs. Run from a private repository if content must stay private until
publication.

### Inputs

| Input | Required / default | Meaning |
| ----- | ------------------ | ------- |
| `announcement` | Required | JSON object described below |
| `channels` | Required | Nonempty JSON array of unique service names |
| `targets` | Required | JSON object mapping service names to destination arrays |
| `mode` | `check` | `check`, `draft`, or `publish` |
| `wait_for_url_minutes` | `0` | Wait up to this many minutes (at most 120) for `url` to return 200 before approval; `0` skips |
| `environment` | `announcements` | Caller environment for approvals and environment secrets |
| `groupsio_host` | `lists.cncf.io` | Either `lists.cncf.io` or `groups.io`; arbitrary hosts are rejected |
| `report_name` | `announcements` | Unique artifact label for multiple calls/matrix entries in the same workflow run |

The supported names are `linkedin`, `bluesky`, `mastodon`, `slack`, and
`groupsio` (note the spelling **mastodon**).

Example `ANNOUNCEMENT_TARGETS` repository variable:

```json
{
  "linkedin": ["BUFFER_LINKEDIN_CHANNEL_ID"],
  "bluesky": ["BUFFER_BLUESKY_CHANNEL_ID"],
  "mastodon": ["BUFFER_MASTODON_CHANNEL_ID"],
  "slack": ["C0123456789", "C9876543210"],
  "groupsio": [12345, 67890]
}
```

These IDs are illustrative, not the organization's actual destinations.
Buffer destinations are connected account/channel IDs, **not** organization IDs;
use a separate ID for each service. Slack destinations are channel IDs, not
names; the webhook URL is a secret, not a target. Groups.io destinations are
positive integer group IDs on the selected host, not email addresses. There is
no implicit destination. Each selected service must have targets. Unselected
services are not contacted. At most 20 destinations may be selected per run.

### Announcement content / file format

Store announcements as UTF-8 JSON files, for example
`.announcements/collector-release.json` in `opentelemetry.io`:

```json
{
  "title": "OpenTelemetry Collector release",
  "text": "A new Collector release is available. Read about the improvements.",
  "url": "https://opentelemetry.io/blog/2026/collector-release/",
  "overrides": {
    "bluesky": {
      "text": "New Collector release! Here is what changed."
    },
    "groupsio": {
      "title": "Collector release announcement",
      "text": "Hello everyone,\n\nA new Collector release is available."
    }
  }
}
```

`title`, `text`, and `url` are required; unknown fields are rejected.
Titles must be nonempty and at most 300 characters; text must be nonempty and at
most 40,000 characters. URLs must be absolute HTTPS URLs without embedded
credentials or whitespace. `overrides` is optional and may replace `title`
and/or `text` for each service. Keep destination selection in the caller's
configuration rather than in content files.

Every channel gets the same structure:

| Channel | Message |
| ------- | ------- |
| LinkedIn, Bluesky, Mastodon | `text`, blank line, `url` |
| Slack | `title`, blank line, `text`, blank line, `url` |
| Groups.io | Subject `title`; body `text`, blank line, `url` |

Groups.io receives escaped HTML, not raw HTML from the file. Slack text escapes
`&`, `<`, and `>`, so content cannot trigger mentions such as `<!channel>`.

The rendered message, including the URL, must fit the channel's limit:
Bluesky 300, Mastodon 500, LinkedIn 3,000, and Slack 40,000 characters. The
check counts code points, which is stricter than Bluesky (graphemes) and
Mastodon (every URL counts as 23). Write `text` as short social copy that fits
Bluesky, and use an override only when a channel needs different wording. This
workflow does not truncate content or create threads. Even a passing check
cannot guarantee that a platform will accept the content.

### Credentials and service setup

| Secret | Required for | Setup |
| ------ | ------------ | ----- |
| `BUFFER_API_KEY` | Buffer drafts/publishing | Generate a Buffer API key with access to the connected OpenTelemetry accounts |
| `SLACK_WEBHOOK_URL` | Slack publishing | A Workflow Builder webhook (`https://hooks.slack.com/triggers/...`); see [Slack setup](#slack-setup). Other URLs are rejected. |
| `GROUPSIO_API_KEY` | Groups.io drafts/publishing | API key for an account subscribed to the target lists, with `can_post` permission |

Use the Groups.io account/API key associated with `lists.cncf.io` and obtain
group IDs on that host. Verify CNCF API access and membership before rollout.
The workflow uses `/newdraft`, `/updatedraft`, and, for publishing only,
`/postdraft`. List moderation still applies; acceptance does not guarantee
immediate email delivery. Review Groups.io drafts in the authenticated account.
If a draft update fails, the report retains its ID so it can be inspected or
removed manually.

#### Slack setup

In Slack Workflow Builder, create a workflow that starts **from a webhook**
and declares two variables: `channel` (type *Slack channel ID*) and `text`
(type *Text*). Add a **Send a message to a channel** step that uses the
`channel` variable as its channel and only the `text` variable as the message.
Publish the workflow and store its webhook URL as `SLACK_WEBHOOK_URL`. No Slack
app installation is required. Messages appear as posted by the workflow, which
belongs to the Slack user who created it, so add a second collaborator.

## First consumer: opentelemetry.io

The integration belongs in `open-telemetry/opentelemetry.io`; adding this shared
workflow does **not** install or enable it there.

### Blog posts

A blog post opts in with an `announcement` block in its front matter. The blurb
is reviewed together with the post in the blog PR:

```yaml
---
title: Announcing the OpenTelemetry Collector v1.0
date: 2026-10-20
announcement:
  text: >-
    The Collector reached v1.0! Here is what stability means for you.
---
```

The caller turns each post into an announcement: `title` from the front matter,
`text` from the block, and `url` from the path
(`content/en/blog/2026/collector-v1/index.md` and
`content/en/blog/2026/collector-v1.md` both become
`https://opentelemetry.io/blog/2026/collector-v1/`). The caller needs a small
script that, for each post path:

- reads the front matter (for example with `yq --front-matter=extract`, which
  GitHub-hosted runners provide);
- skips posts with `draft: true` or without an `announcement` block;
- rejects an `announcement` block with anything other than a `text` string,
  and posts that set `slug` or `url`, because the URL is derived from the path;
- rejects paths with characters other than lowercase letters, digits, and
  hyphens, so the derived URL matches Hugo's;
- writes `entries`, a JSON array of `{"name", "path", "content"}`, to
  `GITHUB_OUTPUT`, where `content` is the announcement object.

The workflows below call it as `.github/scripts/blog_announcements.py`. This
assumes posts go live when they are merged; future-dated posts would need a
scheduled job that detects publication instead.

**Pull requests** validate new or changed posts. Nothing is sent, no secrets
are used, and the job summary shows each channel's message:

```yaml
name: Check blog announcements
on:
  pull_request:
    paths: ["content/en/blog/**"]

permissions:
  contents: read

jobs:
  prepare:
    runs-on: ubuntu-latest
    outputs:
      entries: ${{ steps.posts.outputs.entries }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - id: posts
        env:
          EVENT_NAME: ${{ github.event_name }}
          BASE: ${{ github.event.pull_request.base.sha }}
          SITE_URL: https://opentelemetry.io
        # Lists added or modified posts between BASE and HEAD.
        run: python3 .github/scripts/blog_announcements.py

  check:
    needs: prepare
    if: needs.prepare.outputs.entries != '[]'
    name: ${{ matrix.entry.path }}
    strategy:
      fail-fast: false
      matrix:
        entry: ${{ fromJSON(needs.prepare.outputs.entries) }}
    uses: open-telemetry/shared-workflows/.github/workflows/announcements.yml@<commit-sha>
    with:
      announcement: ${{ toJSON(matrix.entry.content) }}
      channels: '["linkedin","bluesky","mastodon","slack","groupsio"]'
      targets: "{}"
      mode: check
```

**Pushes to `main`** announce newly added posts. A merge does not mean the post
is deployed, so `wait_for_url_minutes` must exceed the site's build time. The
`check` job waits for the page, and the approval request arrives once the link
works. If the page is not live in time, the run fails and nothing is sent;
re-run it after the deploy. `workflow_dispatch` repeats a single post:

```yaml
name: Announce blog posts
on:
  push:
    branches: [main]
    paths: ["content/en/blog/**"]
  workflow_dispatch:
    inputs:
      post:
        description: Post path, for example content/en/blog/2026/example/index.md
        required: true
        type: string
      mode:
        type: choice
        options: [check, draft, publish]
        default: check

permissions:
  contents: read

jobs:
  prepare:
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    outputs:
      entries: ${{ steps.posts.outputs.entries }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - id: posts
        env:
          EVENT_NAME: ${{ github.event_name }}
          POST: ${{ inputs.post }}
          BASE: ${{ github.event.before }}
          SITE_URL: https://opentelemetry.io
        # Lists posts added between BASE and GITHUB_SHA. When BASE is all
        # zeros (a new branch), it announces nothing.
        run: python3 .github/scripts/blog_announcements.py

  announce:
    needs: prepare
    if: needs.prepare.outputs.entries != '[]'
    name: ${{ matrix.entry.path }}
    strategy:
      fail-fast: false
      max-parallel: 1
      matrix:
        entry: ${{ fromJSON(needs.prepare.outputs.entries) }}
    uses: open-telemetry/shared-workflows/.github/workflows/announcements.yml@<commit-sha>
    with:
      announcement: ${{ toJSON(matrix.entry.content) }}
      channels: ${{ vars.ANNOUNCEMENT_CHANNELS }}
      targets: ${{ vars.ANNOUNCEMENT_TARGETS }}
      mode: ${{ inputs.mode || 'draft' }}
      wait_for_url_minutes: 30
      report_name: ${{ matrix.entry.name }}
    secrets:
      BUFFER_API_KEY: ${{ secrets.BUFFER_API_KEY }}
      SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
      GROUPSIO_API_KEY: ${{ secrets.GROUPSIO_API_KEY }}
```

Edited and renamed posts are not announced again.

### Standalone announcements

For announcements without a blog post, store the announcement as a JSON file
(see [Announcement content](#announcement-content--file-format)) and react to
**new** files on `main`. Automatic runs use `draft`; manual runs default to
`check`. Set `ANNOUNCEMENT_CHANNELS` to a JSON array and `ANNOUNCEMENT_TARGETS`
to the destination object above.

```yaml
name: Announce new files
on:
  push:
    branches: [main]
    paths: [".announcements/*.json"]
  workflow_dispatch:
    inputs:
      file:
        description: "Announcement JSON path under .announcements/"
        required: true
        type: string
      mode:
        type: choice
        options: [check, draft, publish]
        default: check

permissions:
  contents: read

jobs:
  prepare:
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    outputs:
      entries: ${{ steps.files.outputs.entries }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - id: files
        env:
          EVENT_NAME: ${{ github.event_name }}
          INPUT_FILE: ${{ inputs.file }}
          BEFORE: ${{ github.event.before }}
        run: |
          python3 - <<'PY'
          import json
          import os
          from pathlib import Path
          import subprocess

          root = Path(".announcements").resolve()
          if os.environ["EVENT_NAME"] == "workflow_dispatch":
              paths = [os.environ["INPUT_FILE"]]
          elif set(os.environ["BEFORE"]) == {"0"}:
              # No previous commit: announcing every file would repeat old ones.
              paths = []
          else:
              result = subprocess.check_output([
                  "git", "diff", "--name-only", "--diff-filter=A", "-z",
                  os.environ["BEFORE"], os.environ["GITHUB_SHA"],
                  "--", ".announcements/*.json",
              ])
              paths = [p for p in result.decode().split("\0") if p]
          entries = []
          for index, name in enumerate(paths):
              path = Path(name).resolve()
              if path.parent != root or path.suffix != ".json":
                  raise ValueError("Expected a JSON file directly under .announcements/")
              entries.append({
                  "name": f"announcement-{index}",
                  "path": name,
                  "content": json.loads(path.read_text(encoding="utf-8")),
              })
          with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
              output.write("entries=" + json.dumps(entries) + "\n")
          PY

  announce:
    needs: prepare
    if: needs.prepare.outputs.entries != '[]'
    # Without a name, the job name would contain the announcement content.
    name: ${{ matrix.entry.path }}
    strategy:
      fail-fast: false
      max-parallel: 1
      matrix:
        entry: ${{ fromJSON(needs.prepare.outputs.entries) }}
    uses: open-telemetry/shared-workflows/.github/workflows/announcements.yml@<commit-sha>
    with:
      announcement: ${{ toJSON(matrix.entry.content) }}
      channels: ${{ vars.ANNOUNCEMENT_CHANNELS }}
      targets: ${{ vars.ANNOUNCEMENT_TARGETS }}
      mode: ${{ inputs.mode || 'draft' }}
      report_name: ${{ matrix.entry.name }}
    secrets:
      BUFFER_API_KEY: ${{ secrets.BUFFER_API_KEY }}
      SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
      GROUPSIO_API_KEY: ${{ secrets.GROUPSIO_API_KEY }}
```

Editing existing files does not trigger a new delivery in this example.
Do not later rerun a draft run in `publish` expecting draft promotion: `publish`
creates new deliveries. Approve existing drafts in Buffer/Groups.io manually,
and explicitly publish Slack separately with only that service selected.

## Results, failures, and retries

Every invocation uploads a `report.json` artifact (14-day retention) and writes
status counts and the rendered content for each selected service to the job
summary. The report is an audit record of each
destination: channel, target, planned action,
`preview`/`drafted`/`submitted`/`failed`/`pending` status, any returned remote
IDs, and a sanitized error. Rendered content, credentials, and raw API responses
are not included. See [Visibility](#visibility) for what a public run exposes.

All content, destinations, and required secrets are validated before requests.
For non-preview Buffer destinations, channel/service identity and availability
are checked before the first write. Other service permission failures can still
occur during delivery. A destination failure is recorded, other destinations are
attempted, and the job fails overall. Reports are updated after each destination;
`pending` means the workflow did not finish an attempt for that destination.
Missing credentials or failed preflight checks prevent all delivery writes.

`submitted` means the service accepted the request, **not** confirmed end-to-end
publication. Buffer may complete publishing asynchronously or report a later
error, and list moderation may delay or reject a submission.
Slack webhooks return no message ID, so Slack entries have no remote ID; check
the channel itself before retrying. For Slack, `submitted` only means the Slack
workflow was triggered. If its message step fails, for example because the
workflow cannot post to the channel, Slack reports the error to the workflow's
owner as a direct message, not to GitHub.

There are **no automatic write retries and no persistent deduplication**.
Timeouts/connection errors can occur after a service accepted a write. Inspect
the report and remote services before retrying; select only destinations known
not to have received it. GitHub reruns, repeated manual invocations, and
concurrent callers can create duplicate messages or drafts. Consumers should
serialize publication events and track announced URLs if they need exactly-once
intent. This workflow cannot provide an atomic transaction across services.

## API references

- [Buffer draft creation](https://developers.buffer.com/examples/create-draft-post.html)
  and [post input](https://developers.buffer.com/types/CreatePostInput.html)
- [Slack Workflow Builder webhook triggers](https://slack.com/help/articles/360041352714-Build-a-workflow--Create-a-workflow-that-starts-outside-of-Slack)
- [Groups.io API: drafts](https://groups.io/api#drafts)

## Local development

The delivery implementation uses the Python standard library and runs on the
GitHub-hosted Ubuntu runner without additional packages.

```sh
python3 -m unittest discover -s .github/scripts/announcements -p 'test_*.py'
```

Tests mock all outbound requests and cover check-mode isolation, native drafts,
channel routing, rendering and length limits, waiting for the URL, service
payloads, and partial-failure reporting.
