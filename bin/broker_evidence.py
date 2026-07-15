#!/usr/bin/env python3
"""Protected HMAC receipts for tracker publications made by the broker."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EvidenceError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def authority(repository: Path) -> tuple[Path, bytes] | None:
    raw = os.environ.get("STARTUP_FACTORY_LIFECYCLE_STATE_ROOT")
    if not raw:
        return None
    root = Path(raw)
    try:
        resolved = root.resolve(strict=True)
        info = resolved.lstat()
    except OSError as exc:
        raise EvidenceError("broker authority root is unavailable: %s" % exc) from exc
    if (
        not root.is_absolute()
        or resolved != root
        or Path(os.path.normpath(str(root))) != root
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid not in {0, os.geteuid()}
    ):
        raise EvidenceError("broker authority root must be canonical private mode 0700")
    try:
        resolved.relative_to(repository.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise EvidenceError("broker authority root must be outside the agent repository")
    key_path = resolved / "record-auth.key"
    try:
        key_info = key_path.lstat()
        if (
            stat.S_ISLNK(key_info.st_mode)
            or not stat.S_ISREG(key_info.st_mode)
            or stat.S_IMODE(key_info.st_mode) != 0o600
            or key_info.st_size != 32
        ):
            raise EvidenceError("broker authority key is unsafe")
        fd = os.open(key_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            key = os.read(fd, 33)
        finally:
            os.close(fd)
    except OSError as exc:
        raise EvidenceError("cannot read broker authority key: %s" % exc) from exc
    if len(key) != 32:
        raise EvidenceError("broker authority key must contain exactly 32 bytes")
    directory = resolved / "broker-publications"
    if directory.exists() or directory.is_symlink():
        directory_info = directory.lstat()
        if (
            stat.S_ISLNK(directory_info.st_mode)
            or not stat.S_ISDIR(directory_info.st_mode)
            or stat.S_IMODE(directory_info.st_mode) != 0o700
        ):
            raise EvidenceError("broker publication directory is unsafe")
    else:
        directory.mkdir(mode=0o700)
    return directory, key


def receipt_path(directory: Path, payload: dict) -> Path:
    identity = {
        name: payload[name]
        for name in ("repository", "workspace", "team", "featureId", "taskId", "deliveryId")
    }
    return directory / (hashlib.sha256(canonical(identity)).hexdigest() + ".json")


def read_regular_json(path: Path, label: str) -> dict:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            raise EvidenceError("%s must be a bounded non-symlink regular file" % label)
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, ValueError) as exc:
        raise EvidenceError("invalid %s: %s" % (label, exc)) from exc
    if not isinstance(value, dict):
        raise EvidenceError("%s must be a JSON object" % label)
    return value


def body_digest(entry: dict) -> str:
    raw_path = entry.get("publishBodyPath") or entry.get("stagedBodyPath")
    if not isinstance(raw_path, str):
        raise EvidenceError("published entry has no final body path")
    path = Path(raw_path)
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 65536:
            raise EvidenceError("published body is unsafe")
        body = path.read_bytes()
    except OSError as exc:
        raise EvidenceError("cannot read published body: %s" % exc) from exc
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    expected = entry.get("publishBodySha256") or entry.get("stagedBodySha256")
    if digest != expected:
        raise EvidenceError("published body digest mismatch")
    return digest


def record(repository: Path, workspace: Path, entry_path: Path) -> dict:
    configured = authority(repository)
    if configured is None:
        return {"protected": False}
    entry = read_regular_json(entry_path, "published broker entry")
    delivery = str(entry.get("deliveryId") or "")
    if entry.get("phase") != "published" or not re.fullmatch(r"delivery-[0-9a-f]{32}", delivery):
        raise EvidenceError("broker entry is not a completed publication")
    final_digest = body_digest(entry)
    payload = {
        "schemaVersion": 1,
        "repository": str(repository.resolve(strict=True)),
        "workspace": str(workspace.resolve(strict=True)),
        "team": entry.get("team"),
        "featureId": entry.get("featureId"),
        "taskId": entry.get("taskId"),
        "marker": entry.get("marker"),
        "deliveryId": delivery,
        "targetStatus": entry.get("targetStatus"),
        "finalBodySha256": final_digest,
        "publishedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    directory, key = configured
    envelope = {"payload": payload}
    envelope["auth"] = "hmac-sha256:" + hmac.new(key, canonical(envelope), hashlib.sha256).hexdigest()
    path = receipt_path(directory, payload)
    if path.exists():
        existing = read_regular_json(path, "protected broker publication")
        if existing.get("payload") != payload:
            # Idempotent retries may differ only in the observation timestamp.
            old = dict(existing.get("payload") or {})
            old.pop("publishedAt", None)
            new = dict(payload)
            new.pop("publishedAt", None)
            if old != new:
                raise EvidenceError("protected broker publication identity collision")
        return existing
    temporary = path.with_name(".%s.tmp.%s.%s" % (path.name, os.getpid(), secrets.token_hex(8)))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(canonical(envelope) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return envelope


def verify_delivery(
    repository: Path,
    workspace: Path,
    *,
    team: str,
    feature: str,
    task: str,
    marker: str,
    delivery: str,
    target_status: object,
    final_body_digest: str,
) -> bool:
    configured = authority(repository)
    if configured is None:
        return False
    directory, key = configured
    material = {
        "repository": str(repository.resolve(strict=True)),
        "workspace": str(workspace.resolve(strict=True)),
        "team": team,
        "featureId": feature,
        "taskId": task,
        "deliveryId": delivery,
    }
    path = directory / (hashlib.sha256(canonical(material)).hexdigest() + ".json")
    envelope = read_regular_json(path, "protected broker publication")
    if set(envelope) != {"payload", "auth"} or not isinstance(envelope.get("payload"), dict):
        return False
    supplied = str(envelope.get("auth") or "")
    unsigned = {"payload": envelope["payload"]}
    expected_auth = "hmac-sha256:" + hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected_auth):
        return False
    payload = envelope["payload"]
    expected = {
        **material,
        "schemaVersion": 1,
        "marker": marker,
        "targetStatus": target_status,
        "finalBodySha256": final_body_digest,
    }
    return all(payload.get(name) == value for name, value in expected.items())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--entry", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(record(args.repo, args.workspace, args.entry), sort_keys=True))
        return 0
    except EvidenceError as exc:
        print("broker-evidence: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
