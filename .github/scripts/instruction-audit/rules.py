"""Read Markdown rules without changing their text."""

import re
from collections import Counter
from dataclasses import dataclass


INSTRUCTIONS = re.compile(
    r"\.github/(?:copilot-instructions\.md|instructions/(?:[\w-]+/)*[\w.-]+\.instructions\.md)",
    re.ASCII,
)
HEADING = re.compile(r" {0,3}#{1,6}(?:[ \t]+|$)")
TITLE = re.compile(r" {0,3}#[ \t]+\S")
ITEM = re.compile(r" {0,3}(?:[-+*]|\d{1,9}[.)])[ \t]+")
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
NON_PROSE = re.compile(r" {4}| {0,3}(?:[\t>|<]|\[[^\]]+\]:|:?-+:?[ \t]*\|)")


@dataclass(frozen=True)
class Rule:
    path: str
    text: str
    first_line: int
    last_line: int
    is_scope: bool = False

    @property
    def anchor(self):
        return {"path": self.path, "text": self.text}


def _blocks(path, document):
    lines = document.splitlines(keepends=True)
    start = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise ValueError("Unclosed instruction front matter")
        start = end + 1
    i = start
    while i < len(lines) and not lines[i].strip():
        i += 1
    scope_start = None
    if i < len(lines) and TITLE.match(lines[i]):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        scope_start = i
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
        is_scope = start == scope_start and not item and not any(NON_PROSE.match(line) for line in lines[start:i])
        yield Rule(path, "".join(lines[start:i]).rstrip("\r\n"), start + 1, i, is_scope)


def blocks(path, document):
    return (rule for rule in _blocks(path, document) if not rule.is_scope)


def remaining_rules(documents, anchor):
    for path in sorted(documents):
        if anchor and path < anchor["path"]:
            continue
        # Scope blocks are still eligible to match a cursor written by an older parser.
        candidates = list(_blocks(path, documents[path]))
        counts = Counter(rule.text for rule in candidates)
        if anchor and path == anchor["path"]:
            if counts[anchor["text"]] != 1:
                continue
            start = next(i for i, rule in enumerate(candidates) if rule.text == anchor["text"])
            candidates = candidates[start + 1:]
        for rule in candidates:
            if not rule.is_scope:
                yield rule, counts[rule.text] == 1
