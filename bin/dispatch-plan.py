#!/usr/bin/env python3
"""Build one deterministic dispatch action plan from a tracker snapshot."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path


MARKER_RE = re.compile(r"^\s*\[([\w-]+)\]")


def last(task: dict, *names: str) -> int:
    index = -1
    for i, comment in enumerate(task.get("comments") or []):
        match = MARKER_RE.match(str(comment.get("body") or ""))
        if match and match.group(1) in names:
            index = i
    return index


def metadata(task: dict) -> dict:
    result = {"parallelSafe": False, "files": [], "resources": [], "track": None}
    text = str(task.get("description") or "")
    for line in text.splitlines():
        match = re.match(r"^\s*(parallel-safe|files|resources|track)\s*:\s*(.+?)\s*$", line, re.I)
        if not match:
            continue
        key, value = match.group(1).lower(), match.group(2).strip()
        if key == "parallel-safe":
            result["parallelSafe"] = value.lower() in {"true", "yes", "1"}
        elif key in {"files", "resources"}:
            result[key] = [item.strip() for item in value.split(",") if item.strip()]
        else:
            result["track"] = value.lower()
    if not result["track"]:
        haystack = "%s\n%s" % (task.get("title") or "", text)
        result["track"] = "frontend" if re.search(r"\b(frontend|client|browser|component|css|ui)\b", haystack, re.I) else "backend"
    return result


def resources(task: dict) -> set[str]:
    data = metadata(task)
    return {"file:" + item for item in data["files"]} | {"resource:" + item for item in data["resources"]}


def claims_conflict(left: set[str], right: set[str]) -> bool:
    if left & right:
        return True
    left_files = [item[5:].rstrip("/") for item in left if item.startswith("file:")]
    right_files = [item[5:].rstrip("/") for item in right if item.startswith("file:")]
    return any(a.startswith(b + "/") or b.startswith(a + "/") for a in left_files for b in right_files)


def current_block_body(task: dict):
    result = None
    for comment in task.get("comments") or []:
        body = str(comment.get("body") or "")
        if any(line.strip().startswith("blocked-by:") for line in body.splitlines()):
            result = body
    return result


def resume_status(task: dict):
    body = current_block_body(task)
    if body is None:
        return None
    for line in body.splitlines():
        if line.strip().startswith("resume-status: "):
            return line.strip()[len("resume-status: ") :].strip()
    return None


def execution_attempt(workdir: Path, task_id: str) -> int:
    import hashlib

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", task_id).strip("-").lower()[:32] or "task"
    key = "%s-%s" % (slug, hashlib.sha256(task_id.encode()).hexdigest()[:10])
    path = workdir / "executions" / (key + ".json")
    try:
        return int(json.loads(path.read_text()).get("attempt") or 1)
    except FileNotFoundError:
        return 1


def emit(*parts) -> None:
    print("\t".join(str(part).replace("\t", " ").replace("\n", " ") for part in parts))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--stuck-minutes", type=int, required=True)
    parser.add_argument("--execution", choices=["sequential", "parallel"], required=True)
    parser.add_argument("--max-active", type=int)
    args = parser.parse_args()

    skill = Path(args.skill)
    workdir = Path(args.workdir)
    board = json.loads((skill / "config" / "statuses.config.json").read_text())
    payload = json.loads((workdir / "tasks.json").read_text())
    tasks = payload.get("tasks") or []
    by_id = {str(task["taskId"]): task for task in tasks}
    terminal = {status["name"] for status in board["tasks"]["statuses"] if status.get("terminal")}
    blocked_transitions = next(
        (set(status["transitions"]) for status in board["tasks"]["statuses"] if status["name"] == "Blocked"),
        set(),
    )

    preset_env = workdir / "preset.env"
    protocol_reviewer = None
    if preset_env.exists():
        match = re.search(r"^PROTOCOL_REVIEWER=(.+)$", preset_env.read_text(), re.M)
        protocol_reviewer = match.group(1) if match else None

    def blockers_terminal(task: dict) -> bool:
        return all(by_id.get(str(blocker), {}).get("status") in terminal for blocker in (task.get("blockedBy") or []))

    no_resume = []
    for task in tasks:
        task_id = str(task["taskId"])
        if task.get("status") == "Blocked" and (task.get("blockedBy") or []) and blockers_terminal(task):
            target = resume_status(task)
            if target in blocked_transitions:
                emit("unblock", task_id, target)
            else:
                if target:
                    print(
                        "dispatch: warning - %s has invalid resume-status '%s' (legal: %s)"
                        % (task_id, target, ", ".join(sorted(blocked_transitions))),
                        file=sys.stderr,
                    )
                no_resume.append(task_id)
                emit("unblock-no-rs", task_id, target or "")
        for blocker in task.get("blockedBy") or []:
            if str(blocker) not in by_id:
                print(
                    "dispatch: warning - %s blockedBy references unknown [task] '%s'" % (task_id, blocker),
                    file=sys.stderr,
                )

    design_queue = [
        str(task["taskId"])
        for task in tasks
        if last(task, "design-note") > last(task, "design-approved", "design-pushback")
    ]
    review_queue, architecture_queue, merge_queue, anomalies = [], [], [], []
    for task in tasks:
        if task.get("status") != "Review":
            continue
        task_id = str(task["taskId"])
        request = last(task, "review-request")
        if request < 0:
            anomalies.append(task_id)
            continue
        findings = last(task, "review-findings")
        if findings > request:
            anomalies.append(task_id)
            continue
        review_approval = last(task, "review-approval")
        architecture_approval = last(task, "architecture-approval")
        if review_approval > request and architecture_approval > request:
            if protocol_reviewer:
                body = str((task.get("comments") or [])[review_approval].get("body") or "")
                signature = re.search(r"(?:\u2014|-)\s*([\w-]+)(?:\s*\((?:posted by[^)]*|as [^)]+)\))?\s*$", body.strip())
                signer = signature.group(1) if signature else None
                if signer != protocol_reviewer:
                    anomalies.append(task_id)
                    print(
                        "dispatch: warning - %s [review-approval] signed by '%s', expected preset final gate '%s'"
                        % (task_id, signer, protocol_reviewer),
                        file=sys.stderr,
                    )
                    continue
            merge_queue.append(task_id)
        else:
            if review_approval <= request:
                review_queue.append(task_id)
            if architecture_approval <= request:
                architecture_queue.append(task_id)

    if design_queue or architecture_queue:
        emit(
            "launch",
            "principal-architect",
            "Dispatch queue - design gates: %s; architecture reviews: %s. Drain every item and exit."
            % (", ".join(design_queue) or "none", ", ".join(architecture_queue) or "none"),
            "|".join(architecture_queue),
        )
    if review_queue:
        emit("launch", "reviewer", "Dispatch queue - [Review]: %s. Drain every item and exit." % ", ".join(review_queue), "|".join(review_queue))
    if merge_queue:
        emit(
            "launch",
            "integrator",
            "Dispatch queue - dual-approved, integrate in dependency order: %s."
            % ", ".join(merge_queue),
            "|".join(merge_queue),
        )

    # Relaunch task-scoped workers that own active implementation or rework.
    for task in tasks:
        if task.get("status") != "Active" or not task.get("assignee"):
            continue
        design_note = last(task, "design-note")
        design_approved = last(task, "design-approved")
        design_pushback = last(task, "design-pushback")
        if design_note >= 0 and (design_approved <= design_note or design_pushback > design_approved):
            continue
        request = last(task, "review-request")
        findings = last(task, "review-findings")
        if request < 0 or findings > request:
            task_id = str(task["taskId"])
            attempt = execution_attempt(workdir, task_id)
            if findings > request:
                attempt += 1
            emit("launch-task", task.get("assignee"), task_id, attempt)

    active_count = sum(1 for task in tasks if task.get("status") == "Active")
    unintegrated = [task for task in tasks if task.get("status") in {"Active", "Review"}]
    held = set().union(*(resources(task) for task in unintegrated)) if unintegrated else set()
    held_unsafe = any(not metadata(task)["parallelSafe"] for task in unintegrated)
    if args.execution == "sequential":
        slots = 0 if unintegrated else 1
    else:
        cap = args.max_active or 2
        slots = max(0, cap - active_count)

    selected_resources = set()
    selected_unsafe = False
    selected_count = 0
    missing_gate, constrained = [], []
    candidates = [
        task
        for task in tasks
        if task.get("status") == "Planned" and not task.get("assignee") and blockers_terminal(task)
    ]
    for task in candidates:
        task_id = str(task["taskId"])
        if slots <= 0:
            constrained.append(task_id)
            continue
        design_note = last(task, "design-note")
        design_approved = last(task, "design-approved")
        design_pushback = last(task, "design-pushback")
        if design_note < 0 or design_approved <= design_note or design_pushback > design_approved:
            missing_gate.append(task_id)
            continue
        data = metadata(task)
        claims = resources(task)
        if args.execution == "parallel":
            if held_unsafe or selected_unsafe:
                constrained.append(task_id)
                continue
            if not data["parallelSafe"]:
                if unintegrated or selected_count or selected_unsafe:
                    constrained.append(task_id)
                    continue
                selected_unsafe = True
            elif claims_conflict(claims, held | selected_resources):
                constrained.append(task_id)
                continue
        role = data["track"] if data["track"] in {"backend", "frontend", "qa"} else "backend"
        emit("claim-task", role, task_id, 1)
        selected_resources |= claims
        selected_count += 1
        slots -= 1

    heartbeat_dir = workdir / "heartbeats"
    stale = []
    if heartbeat_dir.is_dir():
        now = time.time()
        stale = [
            path.name
            for path in heartbeat_dir.iterdir()
            if now - path.stat().st_mtime > args.stuck_minutes * 60
        ]

    if missing_gate or constrained or stale or anomalies or no_resume:
        detail = "Lead-actionable - missing design gates: %s; constrained ready tasks: %s; stale: %s" % (
            ", ".join(missing_gate) or "none",
            ", ".join(constrained) or "none",
            ", ".join(stale) or "none",
        )
        if anomalies:
            detail += "; anomalous [Review]: %s" % ", ".join(anomalies)
        if no_resume:
            detail += "; blocked without valid resume-status: %s" % ", ".join(no_resume)
        emit("launch", "team-lead", detail + ". One supervision pass, then exit.")


if __name__ == "__main__":
    main()
