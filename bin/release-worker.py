#!/usr/bin/env python3
"""Detached, bounded runner for one idempotent release reconciliation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_IDENTITY_FIELDS = {
    "jobId",
    "repository",
    "runId",
    "team",
    "featureId",
    "attempt",
    "commandDigest",
}
LIFECYCLE_HELPER = Path(__file__).resolve().with_name("process-lifecycle.py")
LIFECYCLE_ENV = {
    "PATH": "/usr/bin:/bin",
    "PYTHONNOUSERSITE": "1",
    "PYTHONSAFEPATH": "1",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def strict_object(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key: %s" % key)
        value[key] = item
    return value


def digest_command(command: list[str]) -> str:
    raw = json.dumps(
        command, sort_keys=False, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def validate_identity(identity: object, command: list[str]) -> dict:
    if (
        not isinstance(identity, dict)
        or set(identity) != EXPECTED_IDENTITY_FIELDS
        or any(
            not isinstance(identity.get(field), str) or not identity[field]
            for field in EXPECTED_IDENTITY_FIELDS - {"attempt"}
        )
        or type(identity.get("attempt")) is not int
        or not 1 <= identity["attempt"] <= 1_000_000
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity["commandDigest"])
        or identity["commandDigest"] != digest_command(command)
    ):
        raise SystemExit("release-worker: identity has an unsupported schema or command")
    material = {
        field: identity[field] for field in EXPECTED_IDENTITY_FIELDS - {"jobId"}
    }
    expected = "release-" + hashlib.sha256(
        json.dumps(
            material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()[:32]
    if identity["jobId"] != expected:
        raise SystemExit("release-worker: identity digest mismatch")
    return identity


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(".%s.tmp.%s" % (path.name, os.getpid()))
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def regular_private_file(path: Path, label: str, *, missing: bool = False) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return False
        raise SystemExit("release-worker: %s is missing" % label)
    except OSError as exc:
        raise SystemExit("release-worker: cannot inspect %s: %s" % (label, exc)) from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise SystemExit("release-worker: %s must be an owner-only regular file" % label)
    return True


def cancellation_requested(path: Path, identity: dict) -> bool:
    if not regular_private_file(path, "cancellation request", missing=True):
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("invalid cancellation request") from exc
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != 1
        or value.get("identity") != identity
        or not isinstance(value.get("requestedAt"), str)
        or value.get("reason") not in {"tracker-authority-changed", "run-paused"}
    ):
        raise RuntimeError("cancellation request identity/schema mismatch")
    return True


def lifecycle_command(
    action: str,
    *,
    lifecycle_root: Path,
    repository: Path,
    identity: dict,
    pid: int | None = None,
    signal_name: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    argv = [
        str(Path(sys.executable).resolve()),
        "-I", "-S", "-E", "-s",
        str(LIFECYCLE_HELPER),
        action,
        "--root", str(lifecycle_root),
        "--repo", str(repository),
    ]
    if action == "register":
        assert pid is not None
        argv += [
            "--team", identity["team"],
            "--category", "release",
            "--instance", identity["jobId"],
            "--kind", "background",
            "--pid", str(pid),
        ]
    else:
        argv += [
            "--team", identity["team"],
            "--category", "release",
            "--instance", identity["jobId"],
        ]
        if action == "signal":
            assert signal_name is not None
            argv += ["--signal", signal_name]
    result = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=LIFECYCLE_ENV,
        timeout=10,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(
            "protected release lifecycle %s failed with exit %s"
            % (action, result.returncode)
        )
    return result


def stop_release(
    process: subprocess.Popen,
    *,
    lifecycle_root: Path,
    repository: Path,
    identity: dict,
    lifecycle_registered: bool,
    grace_seconds: float = 10.0,
) -> int:
    def send(signal_name: str, signal_number: int) -> None:
        if lifecycle_registered:
            result = lifecycle_command(
                "signal",
                lifecycle_root=lifecycle_root,
                repository=repository,
                identity=identity,
                signal_name=signal_name,
                check=False,
            )
            if result.returncode == 0:
                return
            if result.returncode == 3 and process.poll() is not None:
                return
            raise RuntimeError(
                "authenticated release lifecycle %s failed with exit %s"
                % (signal_name, result.returncode)
            )
        # Registration failures happen while the trusted wrapper is still
        # blocked on the inherited launch pipe. It cannot have executed release
        # code, and remains this worker's unreaped child in its dedicated group.
        if process.poll() is not None:
            return
        try:
            if os.getpgid(process.pid) != process.pid or os.getsid(process.pid) != process.pid:
                raise RuntimeError("unregistered release wrapper lacks a dedicated session")
            os.killpg(process.pid, signal_number)
        except ProcessLookupError:
            return

    send("TERM", signal.SIGTERM)
    try:
        return process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        send("KILL", signal.SIGKILL)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("release process leader survived SIGKILL") from exc
        return 137


def retire_release_group(
    *, lifecycle_root: Path, repository: Path, identity: dict
) -> None:
    """Kill same-session descendants and retire their authenticated record."""
    probe = lifecycle_command(
        "probe",
        lifecycle_root=lifecycle_root,
        repository=repository,
        identity=identity,
        check=False,
    )
    if probe.returncode not in {0, 3}:
        raise RuntimeError("protected release lifecycle probe failed")
    if probe.returncode == 0:
        killed = lifecycle_command(
            "signal",
            lifecycle_root=lifecycle_root,
            repository=repository,
            identity=identity,
            signal_name="KILL",
            check=False,
        )
        if killed.returncode not in {0, 3}:
            raise RuntimeError("protected release descendant cleanup failed")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            probe = lifecycle_command(
                "probe",
                lifecycle_root=lifecycle_root,
                repository=repository,
                identity=identity,
                check=False,
            )
            if probe.returncode == 3:
                break
            if probe.returncode != 0:
                raise RuntimeError("protected release lifecycle probe became invalid")
            time.sleep(0.05)
        if probe.returncode == 0:
            raise RuntimeError("release process group survived SIGKILL")
    forgotten = lifecycle_command(
        "forget",
        lifecycle_root=lifecycle_root,
        repository=repository,
        identity=identity,
        check=False,
    )
    if forgotten.returncode:
        raise RuntimeError("protected release lifecycle record could not be retired")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--identity-json", required=True)
    parser.add_argument("--lifecycle-root", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.timeout < 60 or args.timeout > 86400:
        raise SystemExit("release-worker: invalid command or timeout")
    try:
        identity = json.loads(args.identity_json, object_pairs_hook=strict_object)
    except ValueError as exc:
        raise SystemExit("release-worker: invalid identity JSON") from exc
    identity = validate_identity(identity, command)
    parent = args.result.parent.resolve(strict=True)
    if args.log.parent.resolve(strict=True) != parent:
        raise SystemExit("release-worker: result and log must share one protected directory")
    if args.result.name != "result.json" or args.log.name != "release.log":
        raise SystemExit("release-worker: result/log names are fixed by the supervisor")
    if parent.name != identity["jobId"]:
        raise SystemExit("release-worker: job directory does not match job identity")
    info = parent.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise SystemExit("release-worker: job directory must be private mode 0700")
    lifecycle_root = args.lifecycle_root.resolve(strict=True)
    repository = args.repository.resolve(strict=True)
    if str(repository) != identity["repository"]:
        raise SystemExit("release-worker: repository does not match job identity")
    regular_private_file(args.result, "initial job result")
    try:
        initial = json.loads(
            args.result.read_text(encoding="utf-8"), object_pairs_hook=strict_object
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit("release-worker: initial job result is invalid") from exc
    if (
        not isinstance(initial, dict)
        or set(initial) != {"schemaVersion", "identity", "state", "createdAt"}
        or initial.get("schemaVersion") != 1
        or initial.get("identity") != identity
        or initial.get("state") != "launching"
        or not isinstance(initial.get("createdAt"), str)
    ):
        raise SystemExit("release-worker: initial job result does not authorize this launch")
    cancel = parent / "cancel.json"

    state = {
        "schemaVersion": 1,
        "identity": identity,
        "state": "running",
        "workerPid": os.getpid(),
        "startedAt": now(),
        "heartbeatAt": now(),
    }
    atomic_json(args.result, state)
    if cancellation_requested(cancel, identity):
        completed_at = now()
        state.update(
            {
                "state": "cancelled",
                "exitCode": 130,
                "completedAt": completed_at,
                "cancelledAt": completed_at,
            }
        )
        atomic_json(args.result, state)
        return 0
    log_fd = os.open(
        args.log,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    exit_code = 125
    timed_out = False
    cancelled = False
    process: subprocess.Popen | None = None
    lifecycle_registered = False
    control_read = -1
    control_write = -1
    try:
        with os.fdopen(log_fd, "a", encoding="utf-8") as log:
            log_fd = -1
            control_read, control_write = os.pipe()
            wrapper = (
                "import os,sys;"
                "fd=int(sys.argv.pop(1));"
                "f=os.fdopen(fd,'r',encoding='ascii');decision=f.readline().strip();f.close();"
                "decision=='go' or sys.exit(130);"
                "os.execvp(sys.argv[1],sys.argv[1:])"
            )
            process = subprocess.Popen(
                [
                    str(Path(sys.executable).resolve()),
                    "-I", "-S", "-E", "-s", "-c", wrapper,
                    str(control_read), *command,
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
                pass_fds=(control_read,),
            )
            os.close(control_read)
            control_read = -1
            state["releasePid"] = process.pid
            lifecycle_command(
                "register",
                lifecycle_root=lifecycle_root,
                repository=repository,
                identity=identity,
                pid=process.pid,
            )
            lifecycle_registered = True
            if cancellation_requested(cancel, identity):
                cancelled = True
                try:
                    os.write(control_write, b"cancel\n")
                except BrokenPipeError:
                    pass
                os.close(control_write)
                control_write = -1
                stop_release(
                    process,
                    lifecycle_root=lifecycle_root,
                    repository=repository,
                    identity=identity,
                    lifecycle_registered=lifecycle_registered,
                )
                exit_code = 130
            else:
                # Persist uncertainty before opening the barrier. A crash after
                # this write can never be misclassified as a safe pre-launch
                # cancellation, even if it occurs immediately around os.write.
                state["releaseMayHaveStartedAt"] = now()
                atomic_json(args.result, state)
                try:
                    os.write(control_write, b"go\n")
                except BrokenPipeError as exc:
                    raise RuntimeError("release launch barrier exited before authorization") from exc
                os.close(control_write)
                control_write = -1
            atomic_json(args.result, state)
            deadline = time.monotonic() + args.timeout
            next_heartbeat = time.monotonic() + 1
            while not cancelled:
                if cancellation_requested(cancel, identity):
                    state["authorityRevokedAt"] = now()
                    observed = process.poll()
                    exit_code = observed if observed is not None else stop_release(
                        process,
                        lifecycle_root=lifecycle_root,
                        repository=repository,
                        identity=identity,
                        lifecycle_registered=lifecycle_registered,
                    )
                    state["workerError"] = (
                        "release authority changed after launch; deployment outcome requires reconciliation"
                    )
                    break
                exit_code = process.poll()
                if exit_code is not None:
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    stop_release(
                        process,
                        lifecycle_root=lifecycle_root,
                        repository=repository,
                        identity=identity,
                        lifecycle_registered=lifecycle_registered,
                    )
                    exit_code = 124
                    break
                if time.monotonic() >= next_heartbeat:
                    state["heartbeatAt"] = now()
                    atomic_json(args.result, state)
                    next_heartbeat = time.monotonic() + 1
                time.sleep(0.2)
            # A release hook must not leave same-session descendants behind.
            retire_release_group(
                lifecycle_root=lifecycle_root,
                repository=repository,
                identity=identity,
            )
            lifecycle_registered = False
    except BaseException as exc:
        if process is not None and process.poll() is None:
            try:
                stop_release(
                    process,
                    lifecycle_root=lifecycle_root,
                    repository=repository,
                    identity=identity,
                    lifecycle_registered=lifecycle_registered,
                )
            except BaseException:
                pass
        if lifecycle_registered:
            try:
                retire_release_group(
                    lifecycle_root=lifecycle_root,
                    repository=repository,
                    identity=identity,
                )
                lifecycle_registered = False
            except BaseException as cleanup_exc:
                state["workerError"] = "%s: %s; cleanup: %s" % (
                    type(exc).__name__, str(exc)[:300], str(cleanup_exc)[:180]
                )
        if "workerError" not in state:
            state["workerError"] = "%s: %s" % (type(exc).__name__, str(exc)[:500])
        exit_code = 125
    finally:
        if control_read >= 0:
            os.close(control_read)
        if control_write >= 0:
            os.close(control_write)
        if log_fd >= 0:
            os.close(log_fd)
    final_state = "cancelled" if cancelled else "completed"
    state.update({"state": final_state, "exitCode": exit_code, "completedAt": now()})
    if cancelled:
        state["cancelledAt"] = state["completedAt"]
    if timed_out:
        state["timedOut"] = True
    atomic_json(args.result, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
