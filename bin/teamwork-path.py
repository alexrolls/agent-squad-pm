#!/usr/bin/env python3
"""Fail-closed path resolution for agent-team workspace state.

The shell entry points intentionally share this resolver so a project cannot
turn TEAMWORK_ROOT into an absolute/traversing path, and no existing symlink can
redirect a managed read or write outside its team isolation boundary.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path, PurePath


TEAM_RE = re.compile(r"^[A-Za-z0-9._-]{1,63}$")


def fail(message: str) -> "NoReturn":
    raise SystemExit("teamwork-path: " + message)


def relative_parts(value: str, label: str) -> tuple[str, ...]:
    if not value:
        fail(f"{label} must not be empty")
    path = PurePath(value)
    if path.is_absolute() or os.path.isabs(value):
        fail(f"{label} must be repository-relative, not absolute")
    if ".." in path.parts:
        fail(f"{label} must not contain '..'")
    return path.parts


def contained(base: Path, candidate: Path, label: str) -> Path:
    base_real = base.resolve(strict=False)
    candidate_real = candidate.resolve(strict=False)
    try:
        inside = os.path.commonpath((str(base_real), str(candidate_real))) == str(base_real)
    except ValueError:
        inside = False
    if not inside or candidate_real == base_real:
        fail(f"{label} resolves outside its managed directory")
    return candidate_real


def reject_symlink_components(base: Path, candidate: Path, label: str) -> None:
    """Reject an existing symlink at any managed path component.

    Containment alone is insufficient: a link from one in-repository team
    workspace to another would remain inside the repository while crossing the
    isolation boundary. Resolve the lexical path relative to the canonical base
    and reject links before returning a path to a caller.
    """
    base_real = base.resolve(strict=False)
    candidate_lexical = Path(os.path.abspath(candidate))
    try:
        relative = candidate_lexical.relative_to(base_real)
    except ValueError:
        fail(f"{label} is not lexically contained in its managed directory")
    current = base_real
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            fail(f"{label} contains forbidden symlink component: {current}")


def workspace(repo_arg: str, root_arg: str, team: str) -> Path:
    repo = Path(repo_arg).resolve(strict=True)
    if team in {".", ".."} or not TEAM_RE.fullmatch(team):
        fail("unsafe team identifier (allowed: letters, digits, dot, underscore, hyphen)")
    parts = relative_parts(root_arg, "TEAMWORK_ROOT")
    candidate = repo.joinpath(*parts, team)
    reject_symlink_components(repo, candidate, "TEAMWORK_ROOT/team")
    return contained(repo, candidate, "TEAMWORK_ROOT/team")


def child(repo_arg: str, workspace_arg: str, relative_arg: str) -> Path:
    repo = Path(repo_arg).resolve(strict=True)
    workspace_path = Path(workspace_arg)
    reject_symlink_components(repo, workspace_path, "team workspace")
    workspace_real = contained(repo, workspace_path, "team workspace")
    parts = relative_parts(relative_arg, "workspace-relative path")
    candidate = workspace_real.joinpath(*parts)
    reject_symlink_components(workspace_real, candidate, "workspace path")
    return contained(workspace_real, candidate, "workspace path")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    workspace_cmd = sub.add_parser("workspace")
    workspace_cmd.add_argument("--repo", required=True)
    workspace_cmd.add_argument("--root", required=True)
    workspace_cmd.add_argument("--team", required=True)

    child_cmd = sub.add_parser("child")
    child_cmd.add_argument("--repo", required=True)
    child_cmd.add_argument("--workspace", required=True)
    child_cmd.add_argument("--relative", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "workspace":
            result = workspace(args.repo, args.root, args.team)
        else:
            result = child(args.repo, args.workspace, args.relative)
    except (OSError, RuntimeError) as exc:
        fail(str(exc))
    print(result)


if __name__ == "__main__":
    main()
