#!/usr/bin/env python3
"""Build one deterministic dispatch action plan from a tracker snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
from product_acceptance import ProductAcceptancePending, evaluate as evaluate_product_acceptance, validate_request
from task_metadata import parse_task_metadata


MARKER_RE = re.compile(r"^\s*\[([\w-]+)\]")


def last(task: dict, *names: str) -> int:
    index = -1
    for i, comment in enumerate(task.get("comments") or []):
        match = MARKER_RE.match(str(comment.get("body") or ""))
        if match and match.group(1) in names:
            index = i
    return index


def metadata(task: dict) -> dict:
    return parse_task_metadata(task.get("description"), task.get("title"))


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


def block_kind(task: dict):
    body = current_block_body(task)
    if body is None:
        return None
    for line in body.splitlines():
        if line.strip().startswith("block-kind: "):
            return line.strip()[len("block-kind: ") :].strip().lower()
    return None


def open_directory(path: Path, label: str) -> int:
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    try:
        return os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"cannot securely open {label}: {exc}") from exc


def open_child_directory(parent_fd: int, name: str, label: str) -> int:
    flags = os.O_RDONLY
    for flag in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"):
        flags |= getattr(os, flag, 0)
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise RuntimeError(f"cannot securely open {label}: {exc}") from exc


def read_regular_at(parent_fd: int, name: str, label: str, limit: int = 64 * 1024 * 1024) -> str:
    flags = os.O_RDONLY
    for flag in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, flag, 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise RuntimeError(f"cannot securely open {label}: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"{label} must be a non-symlink regular file")
        if info.st_size > limit:
            raise RuntimeError(f"{label} exceeds the {limit}-byte safety limit")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > limit:
            raise RuntimeError(f"{label} exceeds the {limit}-byte safety limit")
        return raw.decode("utf-8")
    finally:
        os.close(descriptor)


def task_key(task_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", task_id).strip("-").lower()[:32] or "task"
    return "%s-%s" % (slug, hashlib.sha256(task_id.encode()).hexdigest()[:10])


def strict_object(raw: str, label: str) -> dict:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise RuntimeError(f"{label} has duplicate field {key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=pairs)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def execution_identity(
    executions_fd: int | None,
    workdir: Path,
    team: str,
    feature_id: str,
    task_id: str,
) -> tuple[str, int] | None:
    key = task_key(task_id)
    if executions_fd is None:
        return None
    try:
        body = read_regular_at(executions_fd, key + ".json", "task execution record", 1024 * 1024)
    except FileNotFoundError:
        return None
    record = strict_object(body, "task execution record")
    role = record.get("role")
    attempt = record.get("attempt")
    if not isinstance(role, str) or not re.fullmatch(r"[a-z0-9-]{1,63}", role):
        raise RuntimeError("task execution record has an unsafe role")
    if type(attempt) is not int or attempt < 1:
        raise RuntimeError("task execution record has an invalid attempt")
    expected = {
        "schemaVersion": 1,
        "featureId": feature_id,
        "taskId": task_id,
        "taskKey": key,
        "branch": f"agent-task/{team}/{key}",
        "worktree": str(workdir / "worktrees" / f"{role}#{attempt}-{key}"),
        "packetPath": str(workdir / "artifacts" / key / f"attempt-{attempt}" / "task-packet.md"),
        "packetJsonPath": str(workdir / "artifacts" / key / f"attempt-{attempt}" / "task-packet.json"),
        "reportPath": str(workdir / "artifacts" / key / f"attempt-{attempt}" / "task-report.md"),
    }
    if any(record.get(name) != value for name, value in expected.items()):
        raise RuntimeError("task execution record does not match its team/feature/task/attempt binding")
    return role, attempt


def claim_identity(
    claims_fd: int | None,
    team: str,
    feature_id: str,
    task: dict,
    working_status: str,
) -> tuple[str, int] | None:
    task_id = str(task["taskId"])
    key = task_key(task_id)
    if claims_fd is None:
        return None
    try:
        body = read_regular_at(claims_fd, key + ".json", "task claim record", 1024 * 1024)
    except FileNotFoundError:
        return None
    record = strict_object(body, "task claim record")
    role = record.get("role")
    attempt = record.get("attempt")
    if not isinstance(role, str) or not re.fullmatch(r"[a-z0-9-]{1,63}", role):
        raise RuntimeError("task claim record has an unsafe role")
    if type(attempt) is not int or attempt < 1:
        raise RuntimeError("task claim record has an invalid attempt")
    expected = {
        "schemaVersion": 1,
        "team": team,
        "featureId": feature_id,
        "taskId": task_id,
        "taskKey": key,
        "attempt": attempt,
        "role": role,
        "targetStatus": working_status,
    }
    expected_claim_id = "dispatch-" + hashlib.sha256(
        "\0".join((team, feature_id, task_id, role, str(attempt), working_status)).encode()
    ).hexdigest()[:32]
    expected["claimId"] = expected_claim_id
    digest = "sha256:" + hashlib.sha256(
        json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    if any(record.get(name) != value for name, value in expected.items()):
        raise RuntimeError("task claim record does not match its team/feature/task/attempt binding")
    if record.get("claimDigest") != digest:
        raise RuntimeError("task claim record digest does not match its immutable identity")
    expected_tail = (
        f"claim-id: {expected_claim_id}\n"
        f"role: {role}\n"
        f"target-status: {working_status}\n\n"
        "— dispatcher"
    )

    def valid_receipt(comment: dict) -> bool:
        body = str(comment.get("body") or "").strip()
        if not body.startswith("[claim]") or "claim-id:" not in body:
            return False
        position = body.find("claim-id:")
        prefix = body[len("[claim]"):position]
        if prefix != "\n" and not re.fullmatch(r" \(\d{4}-\d{2}-\d{2}\): ", prefix):
            return False
        return body[position:] == expected_tail

    matching = [
        comment
        for comment in task.get("comments") or []
        if valid_receipt(comment)
    ]
    if len(matching) != 1:
        raise RuntimeError("active task claim lacks one exact tracker-side claim receipt")
    return role, attempt


def emit(*parts) -> None:
    print("\t".join(str(part).replace("\t", " ").replace("\n", " ") for part in parts))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--team", required=True)
    parser.add_argument("--feature", required=True)
    parser.add_argument("--stuck-minutes", type=int, required=True)
    parser.add_argument("--execution", choices=["sequential", "parallel"], required=True)
    parser.add_argument("--max-active", type=int)
    args = parser.parse_args()

    skill = Path(args.skill)
    workdir = Path(args.workdir)
    workdir_fd = open_directory(workdir, "team workspace")
    board = json.loads((skill / "config" / "statuses.config.json").read_text())
    payload = json.loads(read_regular_at(workdir_fd, "tasks.json", "tracker snapshot"))
    try:
        executions_fd = open_child_directory(workdir_fd, "executions", "execution-record directory")
    except FileNotFoundError:
        executions_fd = None
    try:
        claims_fd = open_child_directory(workdir_fd, "claims", "claim-record directory")
    except FileNotFoundError:
        claims_fd = None
    tasks = payload.get("tasks") or []
    if str(payload.get("featureId") or "") != args.feature:
        raise RuntimeError("tracker snapshot featureId does not match the dispatcher invocation")
    by_id = {str(task["taskId"]): task for task in tasks}
    terminal = {status["name"] for status in board["tasks"]["statuses"] if status.get("terminal")}
    by_kind = {
        status.get("kind"): status["name"]
        for status in board["tasks"]["statuses"]
        if status.get("kind")
    }
    queued_status = by_kind.get("queued", "Planned")
    working_status = by_kind.get("working", "Active")
    review_status = by_kind.get("review", "Review")
    blocked_status = by_kind.get("blocked", "Blocked")
    blocked_transitions = next(
        (set(status["transitions"]) for status in board["tasks"]["statuses"] if status["name"] == "Blocked"),
        set(),
    )

    protocol_reviewer = None
    protocol_product_manager = None
    try:
        preset_text = read_regular_at(workdir_fd, "preset.env", "team preset", 1024 * 1024)
    except FileNotFoundError:
        preset_text = None
    if preset_text is not None:
        match = re.search(r"^PROTOCOL_REVIEWER=(.+)$", preset_text, re.M)
        protocol_reviewer = match.group(1) if match else None
        match = re.search(r"^PROTOCOL_PRODUCT_MANAGER=(.+)$", preset_text, re.M)
        protocol_product_manager = match.group(1) if match and match.group(1) != "null" else None

    def blockers_terminal(task: dict) -> bool:
        return all(by_id.get(str(blocker), {}).get("status") in terminal for blocker in (task.get("blockedBy") or []))

    no_resume, manual_blocks = [], []
    for task in tasks:
        task_id = str(task["taskId"])
        if task.get("status") == blocked_status and (task.get("blockedBy") or []) and blockers_terminal(task):
            kind = block_kind(task)
            if kind != "dependency":
                manual_blocks.append(task_id)
                emit("blocked-sensitive", task_id, kind or "missing")
                continue
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
        if task.get("status") != review_status:
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

    # The release executor creates this exact request only after recomputing the
    # closed integration chain.  Tracker containers are not uniformly
    # commentable, so the feature-level verdict lives on its deterministic
    # anchor task and is routed here like every other gate queue.
    product_closeout_role = None
    product_closeout_detail = None
    product_request = workdir / "product-acceptance-request.json"
    all_terminal = bool(tasks) and all(task.get("status") in terminal for task in tasks)
    if all_terminal:
        try:
            request_text = read_regular_at(
                workdir_fd, "product-acceptance-request.json", "product-acceptance request", 1024 * 1024
            )
        except FileNotFoundError:
            request_text = None
        except (OSError, RuntimeError) as exc:
            product_closeout_role = "team-lead"
            product_closeout_detail = (
                "Product-acceptance request is invalid or ambiguous (%s); repair the deterministic release handoff, "
                "do not author an approval from guessed values." % str(exc)[:300]
            )
            request_text = None
        if request_text is not None:
            try:
                request = json.loads(request_text)
                if not isinstance(request, dict):
                    raise ProductAcceptancePending("request must be a JSON object")
                validate_request(request, payload)
                try:
                    evaluate_product_acceptance(
                        payload,
                        feature_id=str(request["featureId"]),
                        commit=str(request["commit"]),
                        integration_evidence_digest=str(request["integrationEvidenceDigest"]),
                    )
                except ProductAcceptancePending as exc:
                    product_closeout_role = "product-manager" if protocol_product_manager else "team-lead"
                    product_closeout_detail = (
                        "Feature closeout is awaiting a mechanically bound product verdict: %s. "
                        "Read canonicalBody from the validated non-symlink request %s and post it unchanged on "
                        "anchor task %s through the standard outbox."
                        % (str(exc)[:300], product_request, request["anchorTaskId"])
                    )
            except (OSError, ValueError, KeyError, ProductAcceptancePending) as exc:
                product_closeout_role = "team-lead"
                product_closeout_detail = (
                    "Product-acceptance request is invalid or ambiguous (%s); repair the deterministic release handoff, "
                    "do not author an approval from guessed values." % str(exc)[:300]
                )

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
    if product_closeout_role and product_closeout_detail:
        emit("launch", product_closeout_role, product_closeout_detail)

    # Relaunch task-scoped workers that own active implementation or rework.
    for task in tasks:
        if task.get("status") != working_status:
            continue
        task_id = str(task["taskId"])
        execution = execution_identity(
            executions_fd, workdir, args.team, args.feature, task_id
        )
        claim = claim_identity(
            claims_fd, args.team, args.feature, task, working_status
        )
        if execution and claim and execution[0] != claim[0]:
            raise RuntimeError("task execution role conflicts with its durable claim role")
        durable = execution or claim
        tracker_role = task.get("assignee")
        if durable is None:
            if not tracker_role:
                # No authenticated local dispatch identity exists. Do not guess a
                # role from a remote adapter that cannot persist assignees.
                continue
            role, attempt = str(tracker_role), 1
        else:
            role, attempt = durable
            if tracker_role and str(tracker_role) != role:
                raise RuntimeError("tracker assignee conflicts with the durable dispatch identity")
        design_note = last(task, "design-note")
        design_approved = last(task, "design-approved")
        design_pushback = last(task, "design-pushback")
        if design_note >= 0 and (design_approved <= design_note or design_pushback > design_approved):
            continue
        request = last(task, "review-request")
        findings = last(task, "review-findings")
        if request < 0 or findings > request:
            if findings > request:
                attempt += 1
            emit("launch-task", role, task_id, attempt)

    active_count = sum(1 for task in tasks if task.get("status") == working_status)
    unintegrated = [task for task in tasks if task.get("status") in {working_status, review_status}]
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
        if task.get("status") == queued_status and not task.get("assignee") and blockers_terminal(task)
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

    stale = []
    try:
        heartbeat_fd = open_child_directory(workdir_fd, "heartbeats", "heartbeat directory")
    except FileNotFoundError:
        heartbeat_fd = None
    if heartbeat_fd is not None:
        now = time.time()
        for name in os.listdir(heartbeat_fd):
            info = os.stat(name, dir_fd=heartbeat_fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                stale.append(name + " (unsafe non-file heartbeat)")
            elif now - info.st_mtime > args.stuck_minutes * 60:
                stale.append(name)

    if missing_gate or constrained or stale or anomalies or no_resume or manual_blocks:
        detail = "Lead-actionable - missing design gates: %s; constrained ready tasks: %s; stale: %s" % (
            ", ".join(missing_gate) or "none",
            ", ".join(constrained) or "none",
            ", ".join(stale) or "none",
        )
        if anomalies:
            detail += "; anomalous [Review]: %s" % ", ".join(anomalies)
        if no_resume:
            detail += "; blocked without valid resume-status: %s" % ", ".join(no_resume)
        if manual_blocks:
            detail += "; approval/policy/incident or unclassified blocks requiring a lead: %s" % ", ".join(manual_blocks)
        emit("launch", "team-lead", detail + ". One supervision pass, then exit.")


if __name__ == "__main__":
    main()
