#!/usr/bin/env python3
"""Shared parsing and low-risk classification for task metadata."""

from __future__ import annotations

import re
from pathlib import PurePosixPath


METADATA_RE = re.compile(
    r"^\s*(track|parallel-safe|files|resources|model-profile)\s*:\s*(.+?)\s*$",
    re.I,
)
FRONTEND_RE = re.compile(r"\b(frontend|client|browser|component|css|ui)\b", re.I)
OBVIOUS_FAST_RE = re.compile(
    r"\b(docs?|documentation|readme|comments?|typos?|spelling|lint|format(?:ting)?)\b",
    re.I,
)
STRUCTURAL_FAST_RE = re.compile(r"\b(rename|copy|config(?:uration)?|constants?|test-only|tests? only)\b", re.I)
DOC_SUFFIXES = {".adoc", ".md", ".mdx", ".rst", ".txt"}
DOC_NAMES = {"changelog", "contributing", "license", "readme"}


def parse_task_metadata(description: object, title: object = "") -> dict:
    text = str(description or "")
    result = {
        "parallelSafe": False,
        "files": [],
        "resources": [],
        "track": None,
        "modelProfile": None,
    }
    aliases = {
        "track": "track",
        "parallel-safe": "parallelSafe",
        "files": "files",
        "resources": "resources",
        "model-profile": "modelProfile",
    }
    for line in text.splitlines():
        match = METADATA_RE.match(line)
        if not match:
            continue
        key = aliases[match.group(1).lower()]
        value = match.group(2).strip()
        if key == "parallelSafe":
            result[key] = value.lower() in {"true", "yes", "1"}
        elif key in {"files", "resources"}:
            result[key] = [item.strip() for item in value.split(",") if item.strip()]
        else:
            result[key] = value.lower()
    if not result["track"]:
        haystack = "%s\n%s" % (title or "", text)
        result["track"] = "frontend" if FRONTEND_RE.search(haystack) else "backend"
    return result


def _is_documentation_file(path: str) -> bool:
    normalized = path.lower().replace("\\", "/")
    parsed = PurePosixPath(normalized)
    stem = parsed.stem.lower()
    return parsed.suffix.lower() in DOC_SUFFIXES or stem in DOC_NAMES or any(
        part in {"doc", "docs", "documentation"} for part in parsed.parts[:-1]
    )


def _is_test_file(path: str) -> bool:
    normalized = path.lower().replace("\\", "/")
    name = PurePosixPath(normalized).name
    wrapped = "/%s/" % normalized.strip("/")
    return (
        "/test/" in wrapped
        or "/tests/" in wrapped
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
    )


def is_fast_task(task: dict, metadata: dict) -> bool:
    """Return true only for clearly bounded, low-risk task shapes."""
    text = "%s\n%s" % (task.get("title") or "", task.get("description") or "")
    files = metadata.get("files") or []
    if metadata.get("resources") or len(files) > 3:
        return False
    if files and all(_is_documentation_file(path) for path in files):
        return True
    if metadata.get("parallelSafe") and files and all(_is_test_file(path) for path in files):
        return True
    if OBVIOUS_FAST_RE.search(text):
        return len(files) <= 2
    return bool(
        metadata.get("parallelSafe")
        and len(files) <= 2
        and STRUCTURAL_FAST_RE.search(text)
    )
