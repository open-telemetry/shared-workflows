"""Select one Markdown block without changing the saved rule text."""

import re
from dataclasses import dataclass


INSTRUCTIONS = re.compile(
    r"\.github/(?:copilot-instructions\.md|instructions/(?:[\w-]+/)*[\w.-]+\.instructions\.md)",
    re.ASCII,
)
HEADING = re.compile(r" {0,3}#{1,6}(?:[ \t]+|$)")
ITEM = re.compile(r" {0,3}(?:[-+*]|\d{1,9}[.)])[ \t]+")
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class Rule:
    path: str
    text: str
    first_line: int
    last_line: int

    @property
    def anchor(self):
        return {"path": self.path, "text": self.text}


def blocks(path, document):
    lines = document.splitlines(keepends=True)
    start = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise ValueError("Unclosed instruction front matter")
        start = end + 1
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip() or HEADING.match(line) or re.fullmatch(r" {0,3}([-*_])\1{2,}\s*", line):
            i += 1
            continue
        if fence := FENCE.match(line):
            i += 1
            close = re.compile(r" {0,3}" + fence[1][0] + r"{" + str(len(fence[1])) + r",}[ \t]*$")
            while i < len(lines):
                last = lines[i].rstrip("\r\n")
                i += 1
                if close.fullmatch(last):
                    break
            continue
        # A setext heading is context, not a rule.
        if i + 1 < len(lines) and re.fullmatch(r" {0,3}(?:=+|-+)\s*", lines[i + 1]):
            i += 2
            continue
        start = i
        item = ITEM.match(line)
        i += 1
        while i < len(lines):
            next_line = lines[i]
            if item and next_line.startswith(" " * item.end()) and next_line.strip():
                i += 1
                continue
            if not next_line.strip():
                if item and i + 1 < len(lines) and lines[i + 1].startswith(" " * item.end()):
                    i += 1
                    continue
                break
            if HEADING.match(next_line) or ITEM.match(next_line) or FENCE.match(next_line):
                break
            i += 1
        yield Rule(path, "".join(lines[start:i]).rstrip("\r\n"), start + 1, i)


def select(documents, anchor):
    for path in sorted(documents):
        if anchor and path < anchor["path"]:
            continue
        candidates = list(blocks(path, documents[path]))
        if anchor and path == anchor["path"]:
            matches = [i for i, rule in enumerate(candidates) if rule.text == anchor["text"]]
            if len(matches) != 1:
                continue
            candidates = candidates[matches[0] + 1:]
        if candidates:
            return candidates[0]
    return None
