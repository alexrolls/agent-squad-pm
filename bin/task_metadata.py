#!/usr/bin/env python3
"""Shared parsing and low-risk classification for task metadata."""

from __future__ import annotations

import re
from pathlib import PurePosixPath


METADATA_RE = re.compile(
    r"^\s*(track|parallel-safe|files|resources|model-profile|work-kind|review-gates)\s*:\s*(.+?)\s*$",
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
SUPPORTED_REVIEW_GATES = ("qa", "security")


def normalize_review_gates(values: list[str] | tuple[str, ...]) -> list[str]:
    normalized = [str(item).strip().lower() for item in values if str(item).strip()]
    if len(normalized) != len(set(normalized)):
        raise ValueError("review gates must not contain duplicates")
    unsupported = sorted(set(normalized) - set(SUPPORTED_REVIEW_GATES))
    if unsupported:
        raise ValueError(
            "review-gates supports only qa and security; got: "
            + ", ".join(unsupported)
        )
    return [gate for gate in SUPPORTED_REVIEW_GATES if gate in normalized]


def required_review_gates(preset_text: object = "") -> list[str]:
    matches = re.findall(
        r"^REQUIRED_REVIEW_GATES=([^\r\n]+)$", str(preset_text or ""), re.M
    )
    if len(matches) > 1:
        raise ValueError("team preset must not duplicate REQUIRED_REVIEW_GATES")
    if not matches or matches[0].strip().lower() == "null":
        return []
    return normalize_review_gates(tuple(matches[0].split(",")))


def effective_review_gates(metadata: dict, preset_text: object = "") -> list[str]:
    combined = set(metadata.get("reviewGates") or ()) | set(
        required_review_gates(preset_text)
    )
    return [gate for gate in SUPPORTED_REVIEW_GATES if gate in combined]


def parse_task_metadata(description: object, title: object = "") -> dict:
    text = str(description or "")
    result = {
        "parallelSafe": False,
        "files": [],
        "resources": [],
        "track": None,
        "modelProfile": None,
        "workKind": None,
        "reviewGates": [],
    }
    aliases = {
        "track": "track",
        "parallel-safe": "parallelSafe",
        "files": "files",
        "resources": "resources",
        "model-profile": "modelProfile",
        "work-kind": "workKind",
        "review-gates": "reviewGates",
    }
    for line in text.splitlines():
        match = METADATA_RE.match(line)
        if not match:
            continue
        key = aliases[match.group(1).lower()]
        value = match.group(2).strip()
        if key == "parallelSafe":
            result[key] = value.lower() in {"true", "yes", "1"}
        elif key in {"files", "resources", "reviewGates"}:
            result[key] = [item.strip() for item in value.split(",") if item.strip()]
            if key == "reviewGates":
                result[key] = normalize_review_gates(tuple(result[key]))
        elif key == "workKind":
            result[key] = value.lower()
            if result[key] not in {"defect", "change", "research", "operations"}:
                raise ValueError(
                    "work-kind must be defect, change, research, or operations"
                )
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
