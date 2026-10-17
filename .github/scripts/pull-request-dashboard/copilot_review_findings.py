"""Extract summary-only Copilot findings for promotion into review threads."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import PurePosixPath
import re

from dashboard_status import promoted_copilot_finding_id
from pull_request_source import Review, ReviewThread


@dataclass(frozen=True)
class CopilotSummaryFinding:
    review_url: str
    head_sha: str
    path: str
    line: int
    title: str
    severity: str
    body: str

    @property
    def identity(self) -> str:
        value = json.dumps(
            [self.head_sha, self.path, self.line, self.title],
            ensure_ascii=True,
        )
        return hashlib.sha256(value.encode()).hexdigest()

    def comment_body(self) -> str:
        severity = f" ({self.severity})" if self.severity else ""
        return (
            f"<!-- pull-request-dashboard-copilot-finding:{self.identity} -->\n\n"
            f"**{self.title}**{severity}\n\n"
            f"`{self.path}:{self.line}`\n\n"
            f"{self.body}\n\n"
            f"Copied from [Copilot's review]({self.review_url}), where this "
            "finding appeared only in the \"previously missed\" summary. "
            "Reply here to discuss or reject it, and resolve this conversation "
            "when it no longer needs attention."
        )


@dataclass
class _Details:
    summary: str = ""
    body: str = ""
    summary_start: int = 0
    body_start: int = 0
    children: list[_Details] = field(default_factory=list)


class _DetailsParser(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=False)
        self.text = text
        self.offsets = [0]
        for line in text.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.sections: list[_Details] = []
        self.stack: list[_Details] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "details":
            section = _Details()
            if self.stack:
                self.stack[-1].children.append(section)
            else:
                self.sections.append(section)
            self.stack.append(section)
        elif tag == "summary" and self.stack:
            self.stack[-1].summary_start = self._offset() + len(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        if tag == "summary" and self.stack:
            section = self.stack[-1]
            section.summary = self.text[section.summary_start:self._offset()]
            section.body_start = self._offset() + len("</summary>")
        elif tag == "details" and self.stack:
            section = self.stack.pop()
            section.body = self.text[section.body_start:self._offset()]


def _summary_text(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", value)).strip()


def _parse_findings(review: Review) -> tuple[CopilotSummaryFinding, ...]:
    if "previously missed" not in review.body.lower():
        return ()
    parser = _DetailsParser(review.body)
    parser.feed(review.body)
    parser.close()
    if parser.stack:
        raise ValueError(f"Copilot review {review.database_id} has unclosed details")
    findings: list[CopilotSummaryFinding] = []
    for section in parser.sections:
        summary = _summary_text(section.summary)
        if "previously missed" not in summary.lower():
            continue
        count_match = re.fullmatch(r"(\d+) previously missed issues?", summary, re.I)
        if count_match is None:
            raise ValueError(
                f"Copilot review {review.database_id} has an unsupported missed-issue summary"
            )
        expected = int(count_match.group(1))
        if len(section.children) != expected:
            raise ValueError(
                f"Copilot review {review.database_id} lists {expected} missed issues "
                f"but contains {len(section.children)} findings"
            )
        for item in section.children:
            location = re.search(r"(?m)^\s*`([^`\r\n]+):(\d+)`\s*$", item.body)
            title = _summary_text(item.summary)
            if location is None or not title:
                raise ValueError(
                    f"Copilot review {review.database_id} has an unsupported finding location or title"
                )
            path = location.group(1).replace("\u200b", "")
            line = int(location.group(2))
            body = item.body[location.end():].strip()
            parsed_path = PurePosixPath(path)
            if (
                not path
                or parsed_path.is_absolute()
                or ".." in parsed_path.parts
                or "\\" in path
                or line <= 0
                or not body
                or not review.url
            ):
                raise ValueError(f"Copilot review {review.database_id} has an invalid finding")
            severity = re.search(r'\balt=["\']([^"\']*severity)["\']', item.summary, re.I)
            findings.append(CopilotSummaryFinding(
                review_url=review.url,
                head_sha=review.commit_id,
                path=path,
                line=line,
                title=title,
                severity=html.unescape(severity.group(1)) if severity else "",
                body=body,
            ))
    return tuple(findings)


def pending_copilot_summary_findings(
    reviews: Sequence[Review],
    head_sha: str,
    review_threads: Sequence[ReviewThread],
) -> tuple[CopilotSummaryFinding, ...]:
    current = [
        review
        for review in reviews
        if review.actor.is_copilot_reviewer
        and head_sha
        and review.commit_id == head_sha
        and review.state not in ("PENDING", "DISMISSED")
        and "<!-- ccr-overview-v2 -->" in review.body
    ]
    if not current:
        return ()
    latest = max(current, key=lambda review: (review.submitted_at, review.database_id))
    promoted = {
        promoted_copilot_finding_id(thread.comments[0].actor.login, thread.comments[0].body)
        for thread in review_threads
        if thread.comments
    }
    # Resolved and outdated copies also suppress reposting on the same head.
    return tuple({
        finding.identity: finding
        for finding in _parse_findings(latest)
        if finding.identity not in promoted
    }.values())
