#!/usr/bin/env python3
"""Deterministic board-wide supervisor for cron, service timers, or a watch loop."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent
COMMAND_TIMEOUT_SECONDS = 120
COMMAND_KILL_GRACE_SECONDS = 5
RELEASE_TIMEOUT_SECONDS = 7200
ACTIVE_TRUSTED_PATH = "/usr/bin:/bin"
TRUSTED_GIT = "/usr/bin/git"
MAX_PROTECTED_HANDOFF_FILE_BYTES = 16 * 1024 * 1024
RELEASE_SNAPSHOT_FILES = {
    "release-feature.py": Path("bin/release-feature.py"),
    "policy-check.py": Path("bin/policy-check.py"),
    "tracker-ops.sh": Path("bin/tracker-ops.sh"),
    "finalize-integrations.sh": Path("bin/finalize-integrations.sh"),
    "runtime-state.py": Path("bin/runtime-state.py"),
    "task_metadata.py": Path("bin/task_metadata.py"),
    "product_acceptance.py": Path("bin/product_acceptance.py"),
    "teamwork-path.py": Path("bin/teamwork-path.py"),
    "review_evidence.py": Path("bin/review_evidence.py"),
    "statuses.config.json": Path("config/statuses.config.json"),
    "guardrails.config.json": Path("config/guardrails.config.json"),
    "team.config.md": Path("config/team.config.md"),
    "project-management.config.md": Path("config/project-management.config.md"),
}


class MonitorError(RuntimeError):
    pass


def unprivileged_git_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Keep scheduler/tracker/cloud credentials out of Git filters and hooks."""
    source = source or dict(os.environ)
    child = {
        name: source[name]
        for name in ("TMPDIR", "LANG", "LC_ALL")
        if name in source
    }
    child["PATH"] = ACTIVE_TRUSTED_PATH
    child["GIT_CONFIG_GLOBAL"] = os.devnull
    child["GIT_CONFIG_NOSYSTEM"] = "1"
    child["PYTHONNOUSERSITE"] = "1"
    child["PYTHONSAFEPATH"] = "1"
    return child


def supervisor_child_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Preserve adapter credentials while removing process-startup injection controls."""
    source = source or dict(os.environ)
    forbidden = {
        "BASH_ENV", "ENV", "CDPATH", "GLOBIGNORE", "SHELLOPTS", "PYTHONHOME",
        "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT", "PYTHONUSERBASE",
        "NODE_OPTIONS", "RUBYOPT", "RUBYLIB", "PERL5OPT", "PERL5LIB",
    }
    child = {
        name: value
        for name, value in source.items()
        if name not in forbidden
        and not name.startswith("PYTHON")
        and not name.startswith(("LD_", "DYLD_", "BASH_FUNC_", "GIT_CONFIG"))
    }
    child["PYTHONNOUSERSITE"] = "1"
    child["PYTHONSAFEPATH"] = "1"
    child["PATH"] = ACTIVE_TRUSTED_PATH
    return child


def git_argv(*args: str) -> list[str]:
    return [TRUSTED_GIT, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args]


def tool_path(env_name: str, default_name: str) -> str:
    return os.environ.get(env_name) or str(SKILL_DIR / "bin" / default_name)


def isolated_release_command(skill_root: Path, script: Path) -> list[str]:
    bootstrap = (
        "import runpy,sys;"
        "root=sys.argv.pop(1);script=sys.argv.pop(1);"
        "sys.path.insert(0,root);"
        "runpy.run_path(script,run_name='__main__')"
    )
    return [
        str(Path(sys.executable).resolve()),
        "-I", "-S", "-E", "-s", "-c", bootstrap,
        str(skill_root), str(script),
    ]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def strict_json(text: str) -> object:
    def object_from_pairs(pairs: list[tuple[str, object]]) -> dict:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(
        text,
        object_pairs_hook=object_from_pairs,
        parse_constant=reject_constant,
    )


def load_json(path: Path, label: str) -> dict:
    try:
        value = strict_json(path.read_text())
    except (OSError, ValueError) as exc:
        raise MonitorError(f"cannot load {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MonitorError(f"{label} must be a JSON object: {path}")
    return value


def canonical_trusted_directories(
    trusted_path: object,
    project: Path | None = None,
) -> list[Path]:
    """Validate PATH directories and collapse protected system symlink aliases."""
    entries = trusted_path.split(":") if isinstance(trusted_path, str) else []
    if (
        not isinstance(trusted_path, str)
        or not trusted_path
        or any(not item.startswith("/") or item in {"/", ".", ".."} for item in entries)
    ):
        raise MonitorError("trustedPath must contain only non-root absolute directory entries")
    canonical: list[Path] = []
    for item in entries:
        directory = Path(item)
        current = Path(directory.anchor)
        for part in directory.parts[1:]:
            current /= part
            try:
                info = current.lstat()
            except OSError as exc:
                raise MonitorError(
                    f"trustedPath directory is unavailable: {directory}: {exc}"
                ) from exc
            if stat.S_ISLNK(info.st_mode):
                # Support OS-owned aliases such as usrmerge /bin -> /usr/bin,
                # but never a scheduler/agent-owned replaceable symlink.
                if info.st_uid != 0:
                    raise MonitorError(
                        f"trustedPath symlink components must be root-owned: {directory}"
                    )
                continue
            if not stat.S_ISDIR(info.st_mode):
                raise MonitorError(f"trustedPath entry is not a directory: {directory}")
            if info.st_uid not in {0, os.geteuid()} or stat.S_IMODE(info.st_mode) & 0o022:
                raise MonitorError(
                    "trustedPath components must be supervisor/root-owned and not "
                    f"group/world writable: {directory}"
                )
        try:
            resolved = directory.resolve(strict=True)
            final_info = resolved.lstat()
        except OSError as exc:
            raise MonitorError(f"trustedPath directory is unavailable: {directory}: {exc}") from exc
        if not stat.S_ISDIR(final_info.st_mode):
            raise MonitorError(f"trustedPath entry is not a directory: {directory}")
        # resolve(strict=True) removes every alias. Revalidate the canonical
        # chain so a protected symlink cannot point into writable storage.
        current = Path(resolved.anchor)
        for part in resolved.parts[1:]:
            current /= part
            info = current.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise MonitorError(
                    "trustedPath canonical components must be supervisor/root-owned, "
                    f"non-symlink directories and not group/world writable: {resolved}"
                )
        if project is not None:
            try:
                resolved.relative_to(project)
            except ValueError:
                pass
            else:
                raise MonitorError(f"trustedPath entry must live outside the agent repository: {directory}")
        if resolved not in canonical:
            canonical.append(resolved)
    return canonical


def configure_trusted_tools(config: dict, project: Path | None = None) -> None:
    """Resolve supervisor tools only from protected, absolute PATH entries."""
    global ACTIVE_TRUSTED_PATH, TRUSTED_GIT
    canonical = canonical_trusted_directories(
        config.get("trustedPath", "/usr/bin:/bin"), project
    )
    for directory in canonical:
        candidate = directory / "git"
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (
            not candidate.is_symlink()
            and stat.S_ISREG(info.st_mode)
            and info.st_uid in {0, os.geteuid()}
            and not stat.S_IMODE(info.st_mode) & 0o022
        ):
            ACTIVE_TRUSTED_PATH = ":".join(str(path) for path in canonical)
            TRUSTED_GIT = str(candidate.resolve())
            return
    raise MonitorError(
        "trustedPath contains no supervisor/root-owned, non-writable, non-symlink Git executable"
    )


def required_project_root() -> Path:
    """Establish the scheduler-selected checkout without executing repository-selected tools."""
    raw = os.environ.get("STARTUP_FACTORY_PROJECT_ROOT")
    if not raw:
        raise MonitorError(
            "STARTUP_FACTORY_PROJECT_ROOT must name the absolute target checkout"
        )
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute() or candidate.is_symlink():
        raise MonitorError(
            "STARTUP_FACTORY_PROJECT_ROOT must be an absolute, non-symlink directory"
        )
    try:
        root = candidate.resolve(strict=True)
        info = root.lstat()
    except OSError as exc:
        raise MonitorError(f"cannot inspect STARTUP_FACTORY_PROJECT_ROOT: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise MonitorError("STARTUP_FACTORY_PROJECT_ROOT must be a directory")
    return root


def require_external_automation_config(config_path: Path, project: Path) -> None:
    try:
        config_path.relative_to(project)
    except ValueError:
        return
    raise MonitorError("automation config must live outside the agent repository")


def verify_project_checkout(project: Path) -> None:
    """Verify the bootstrap path with Git only after its executable is trusted."""
    try:
        result = subprocess.run(
            git_argv("rev-parse", "--show-toplevel"),
            cwd=project,
            capture_output=True,
            text=True,
            env=unprivileged_git_environment(),
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise MonitorError("git project-root verification exceeded its operation deadline") from exc
    if result.returncode:
        raise MonitorError(f"project root is not a Git checkout: {result.stderr.strip()}")
    try:
        reported = Path(result.stdout.strip()).resolve(strict=True)
    except OSError as exc:
        raise MonitorError(f"Git returned an invalid project root: {exc}") from exc
    if reported != project:
        raise MonitorError(
            "STARTUP_FACTORY_PROJECT_ROOT must be the exact Git checkout root"
        )


def contained(root: Path, relative: str, label: str) -> Path:
    relative_path = Path(relative)
    if not relative or relative_path.is_absolute() or ".." in relative_path.parts:
        raise MonitorError(f"{label} must be a non-empty repository-relative path")
    candidate = root.joinpath(*relative_path.parts)
    current = root.resolve()
    for part in relative_path.parts:
        current = current / part
        if current.is_symlink():
            raise MonitorError(f"{label} must not traverse symlinks: {current}")
    path = candidate.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise MonitorError(f"{label} escapes the repository: {relative}") from exc
    return path


def managed_path(root: Path, *parts: str, label: str) -> Path:
    """Return a child path while rejecting every existing symlink component."""
    root = root.resolve()
    candidate = root.joinpath(*parts)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise MonitorError(f"{label} escapes its managed root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise MonitorError(f"{label} must not traverse symlinks: {current}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise MonitorError(f"{label} escapes its managed root") from exc
    return resolved


def run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    label: str,
    check: bool = True,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    deadline = timeout_seconds or COMMAND_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise MonitorError(f"{label} could not start: {exc.strerror or 'operating-system error'}") from exc
    try:
        stdout, stderr = process.communicate(timeout=deadline)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=COMMAND_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        raise MonitorError(
            f"{label} exceeded the {deadline}s operation deadline"
        ) from exc
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.returncode and result.stderr.strip():
        print(result.stderr.rstrip(), file=sys.stderr)
    if check and result.returncode:
        raise MonitorError(f"{label} failed with exit {result.returncode}")
    return result


class Lease:
    def __init__(self, path: Path, stale_seconds: int):
        self.path = path
        self.stale_seconds = stale_seconds
        self.owned = False

    def _owner_alive(self, value: dict) -> bool:
        if value.get("host") != socket.gethostname():
            return False
        try:
            pid = int(value.get("pid"))
            os.kill(pid, 0)
            return True
        except (TypeError, ValueError, ProcessLookupError, PermissionError):
            return False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.mkdir(mode=0o700)
        except FileExistsError:
            owner_path = self.path / "owner.json"
            try:
                owner = load_json(owner_path, "monitor lease")
                acquired = datetime.fromisoformat(str(owner.get("acquiredAt")))
                if acquired.tzinfo is None:
                    acquired = acquired.replace(tzinfo=timezone.utc)
                age = (utc_now() - acquired).total_seconds()
            except (MonitorError, TypeError, ValueError):
                owner, age = {}, self.stale_seconds + 1
            if self._owner_alive(owner) or age <= self.stale_seconds:
                return False
            # The lease directory is generated and must contain only this file.
            entries = list(self.path.iterdir())
            if any(entry.name != "owner.json" or not entry.is_file() or entry.is_symlink() for entry in entries):
                raise MonitorError(f"refusing unsafe stale-lease recovery at {self.path}")
            for entry in entries:
                entry.unlink()
            self.path.rmdir()
            self.path.mkdir(mode=0o700)
        owner = {"schemaVersion": 1, "pid": os.getpid(), "host": socket.gethostname(), "acquiredAt": iso_now()}
        (self.path / "owner.json").write_text(json.dumps(owner, indent=2) + "\n")
        self.owned = True
        return True

    def release(self) -> None:
        if not self.owned:
            return
        owner = self.path / "owner.json"
        if owner.is_file() and not owner.is_symlink():
            owner.unlink()
        self.path.rmdir()
        self.owned = False


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def read_teamwork_root() -> str:
    team_config = (SKILL_DIR / "config" / "team.config.md").resolve()
    raw, _ = capture_protected_file(team_config, "team config")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MonitorError("team config must be UTF-8 text") from exc
    match = re.search(r"^TEAMWORK_ROOT=([^\s#]+)", text, re.MULTILINE)
    value = (match.group(1).strip('"') if match else ".teamwork")
    return ".teamwork" if value in {"", "null"} else value


def parse_key_values(text: str, path: Path) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for match in re.finditer(r"^([A-Z_]+)=(.*)$", text, re.MULTILINE):
        if match.group(1) in values:
            raise MonitorError(
                f"duplicate configuration key {match.group(1)} in {path}; "
                "safety settings must have one unambiguous value"
            )
        value = match.group(2).split("#", 1)[0].strip().strip('"')
        values[match.group(1)] = None if value == "null" else value
    return values


def read_key_values(path: Path) -> dict[str, str | None]:
    try:
        text = path.read_text()
    except OSError as exc:
        raise MonitorError(f"cannot read project-management config {path}: {exc}") from exc
    return parse_key_values(text, path)


def integer_setting(
    config: dict,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = config.get(name, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise MonitorError(
            f"{name} must be an integer from {minimum} to {maximum}"
        )
    return value


def scan_interval_seconds(config: dict) -> int:
    """Return the board scan interval, preferring the minute-based contract."""
    has_minutes = "scanIntervalMinutes" in config
    has_legacy_seconds = "pollSeconds" in config
    if has_minutes and has_legacy_seconds:
        raise MonitorError(
            "set only scanIntervalMinutes; pollSeconds is a legacy compatibility setting"
        )
    if has_minutes:
        return integer_setting(config, "scanIntervalMinutes", 3, 1, 1440) * 60
    if has_legacy_seconds:
        return integer_setting(config, "pollSeconds", 180, 1, 86400)
    return 3 * 60


def validate_pm_automation(project: Path) -> None:
    path = Path(os.environ.get("STARTUP_FACTORY_PM_CONFIG") or SKILL_DIR / "config" / "project-management.config.md").expanduser()
    if not path.is_absolute() or path.is_symlink():
        raise MonitorError("project-management config must be an absolute, non-symlink protected file")
    resolved = path.resolve()
    try:
        resolved.relative_to(project)
    except ValueError:
        pass
    else:
        raise MonitorError("project-management config must live outside the agent repository")
    raw, _ = capture_protected_file(resolved, "project-management config")
    try:
        values = parse_key_values(raw.decode("utf-8"), resolved)
    except UnicodeDecodeError as exc:
        raise MonitorError("project-management config must be UTF-8 text") from exc
    if values.get("TEAM_MODE") != "true":
        raise MonitorError("portfolio automation requires TEAM_MODE=true")
    adapter = os.environ.get("TRACKER_ADAPTER") or values.get("PRODUCT_MANAGEMENT_TOOL")
    if adapter == "Linear":
        if values.get("LINEAR_ACCESS") != "rest":
            raise MonitorError("Linear cron/service automation requires LINEAR_ACCESS=rest")
        if not values.get("LINEAR_DEFAULT_TEAM"):
            raise MonitorError("Linear portfolio automation requires an explicit LINEAR_DEFAULT_TEAM scope")
    if adapter == "Jira":
        if values.get("JIRA_ACCESS") != "rest":
            raise MonitorError("Jira cron/service automation requires JIRA_ACCESS=rest")
        if not values.get("JIRA_PROJECT_KEY"):
            raise MonitorError("Jira portfolio automation requires an explicit JIRA_PROJECT_KEY scope")
        jira_task_type = str(values.get("JIRA_TASK_ISSUE_TYPE") or "")
        if not jira_task_type or jira_task_type.casefold() == "epic":
            raise MonitorError(
                "Jira portfolio automation requires a non-Epic JIRA_TASK_ISSUE_TYPE child scope"
            )
    if adapter == "GitHubIssues":
        if values.get("GITHUB_USE_MCP") == "true":
            raise MonitorError("GitHub cron/service automation requires GITHUB_USE_MCP=false")
        repository = values.get("GITHUB_REPO") or ""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise MonitorError(
                "GitHub portfolio automation requires an explicit GITHUB_REPO=owner/repository scope"
            )


def meaningful_command(value: str | None) -> bool:
    """Reject empty and explicit no-op commands as autonomous safety evidence."""
    normalized = re.sub(r"\s+", " ", str(value or "").strip()).rstrip(";").strip().lower()
    return bool(normalized) and normalized not in {":", "true", "/bin/true", "exit 0"}


def validate_agent_sandbox_runner(project: Path, values: dict[str, str | None]) -> None:
    raw = values.get("AGENT_SANDBOX_RUNNER") or ""
    runner = Path(raw)
    if not raw:
        raise MonitorError(
            "autonomous launch requires AGENT_SANDBOX_RUNNER to name a protected external executable"
        )
    if not runner.is_absolute():
        raise MonitorError("AGENT_SANDBOX_RUNNER must be an absolute path")
    try:
        metadata = runner.lstat()
    except OSError as exc:
        raise MonitorError(f"cannot stat AGENT_SANDBOX_RUNNER {runner}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise MonitorError("AGENT_SANDBOX_RUNNER must not be a symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise MonitorError("AGENT_SANDBOX_RUNNER must be a regular file")
    if not metadata.st_mode & 0o111 or not os.access(runner, os.X_OK):
        raise MonitorError("AGENT_SANDBOX_RUNNER must be executable")
    if metadata.st_uid not in {0, os.geteuid()}:
        raise MonitorError("AGENT_SANDBOX_RUNNER must be owned by the executor or root")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise MonitorError("AGENT_SANDBOX_RUNNER must not be group- or world-writable")
    try:
        resolved = runner.resolve(strict=True)
        resolved.relative_to(project.resolve(strict=True))
    except ValueError:
        return
    except OSError as exc:
        raise MonitorError(f"cannot resolve AGENT_SANDBOX_RUNNER {runner}: {exc}") from exc
    raise MonitorError("AGENT_SANDBOX_RUNNER must be external to the agent repository")


def validate_lifecycle_state_root(
    project: Path, values: dict[str, str | None]
) -> Path:
    raw = os.environ.get("STARTUP_FACTORY_LIFECYCLE_STATE_ROOT") or values.get(
        "BROKER_LIFECYCLE_ROOT"
    )
    if not raw:
        raise MonitorError(
            "autonomous launch requires BROKER_LIFECYCLE_ROOT or "
            "STARTUP_FACTORY_LIFECYCLE_STATE_ROOT"
        )
    root = Path(raw)
    if not root.is_absolute() or Path(os.path.normpath(str(root))) != root:
        raise MonitorError(
            "autonomous lifecycle state root must be an absolute normalized path"
        )
    current = Path(root.anchor)
    components = [current]
    for part in root.parts[1:]:
        current /= part
        components.append(current)
    for current in components:
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise MonitorError(
                f"cannot stat lifecycle state path component {current}: {exc}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise MonitorError(
                f"lifecycle state path components must be non-symlink directories: {current}"
            )
        if metadata.st_uid not in {0, os.geteuid()} or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise MonitorError(
                "lifecycle state path components must be broker/root-owned and not "
                f"group/world-writable: {current}"
            )
    if stat.S_IMODE(root.lstat().st_mode) != 0o700:
        raise MonitorError("autonomous lifecycle state root must have mode 0700")
    try:
        resolved = root.resolve(strict=True)
        boundaries = (project.resolve(strict=True), SKILL_DIR.resolve(strict=True))
    except OSError as exc:
        raise MonitorError(f"cannot resolve autonomous lifecycle state root: {exc}") from exc
    for boundary in boundaries:
        try:
            common = Path(os.path.commonpath((str(resolved), str(boundary))))
        except ValueError:
            continue
        if common in {resolved, boundary}:
            raise MonitorError(
                "lifecycle state root must be disjoint from both the agent repository "
                "and the mounted skill installation"
            )
    if not os.access(resolved, os.R_OK | os.W_OK | os.X_OK):
        raise MonitorError("lifecycle state root is not accessible to the broker executor")
    return resolved


def validate_team_safety(automation: dict, project: Path) -> Path:
    for key in ("requireAgentSandbox", "requireSingleTrackerWriter"):
        if automation.get(key) is not True:
            raise MonitorError(
                f"{key} is a mandatory autonomous safety invariant and cannot be disabled or omitted"
            )
    team_config = (SKILL_DIR / "config" / "team.config.md").resolve()
    try:
        team_config.relative_to(project)
    except ValueError:
        pass
    else:
        raise MonitorError("autonomous team config must live outside the agent repository")
    raw, _ = capture_protected_file(team_config, "team config")
    try:
        values = parse_key_values(raw.decode("utf-8"), team_config)
    except UnicodeDecodeError as exc:
        raise MonitorError("team config must be UTF-8 text") from exc
    if values.get("AGENT_SANDBOX_ENFORCED") != "true":
        raise MonitorError(
            "autonomous launch requires AGENT_SANDBOX_ENFORCED=true so all agent execution uses the protected runner"
        )
    validate_agent_sandbox_runner(project, values)
    lifecycle_root = validate_lifecycle_state_root(project, values)
    if values.get("TRACKER_WRITERS") != "broker":
        raise MonitorError(
            "autonomous launch requires TRACKER_WRITERS=broker so no LLM agent receives tracker credentials"
        )
    if not meaningful_command(values.get("WORKTREE_SETUP")):
        raise MonitorError(
            "autonomous launch requires a meaningful WORKTREE_SETUP command; bare worktrees are not eligible"
        )
    configured_validations = [
        values.get(key)
        for key in (
            "VALIDATE_SCRIPT",
            "VALIDATE_BUILD",
            "VALIDATE_TEST",
            "VALIDATE_LINT",
            "VALIDATE_FORMAT",
        )
    ]
    if not any(meaningful_command(command) for command in configured_validations):
        raise MonitorError(
            "autonomous launch requires at least one meaningful VALIDATE_SCRIPT/BUILD/TEST/LINT/FORMAT command"
        )
    return lifecycle_root


def capture_protected_file(
    path: Path,
    label: str,
    expected_digest: str | None = None,
) -> tuple[bytes, str]:
    """Read stable bytes through one no-follow descriptor and verify ownership/mode."""
    if not path.is_absolute():
        raise MonitorError(f"{label} must be an absolute path")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MonitorError(f"cannot securely open {label}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise MonitorError(f"{label} must be a non-symlink regular file")
        if before.st_size < 0 or before.st_size > MAX_PROTECTED_HANDOFF_FILE_BYTES:
            raise MonitorError(
                f"{label} exceeds the {MAX_PROTECTED_HANDOFF_FILE_BYTES}-byte handoff limit"
            )
        if before.st_uid not in {0, os.geteuid()} or stat.S_IMODE(before.st_mode) & 0o022:
            raise MonitorError(
                f"{label} must be owned by the supervisor/root and not group/world writable"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        value = b"".join(chunks)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise MonitorError(f"{label} changed while it was being captured")
    finally:
        os.close(descriptor)
    observed = "sha256:" + hashlib.sha256(value).hexdigest()
    if expected_digest is not None:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest):
            raise MonitorError(f"{label} needs a pinned sha256 digest")
        if observed != expected_digest:
            raise MonitorError(f"{label} digest does not match the protected deployment config")
    return value, observed


def private_directory(path: Path, label: str) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise MonitorError(f"cannot create {label}: {exc}") from exc
    try:
        info = path.lstat()
    except OSError as exc:
        raise MonitorError(f"cannot inspect {label}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise MonitorError(f"{label} must be a non-symlink directory")
    if info.st_uid not in {0, os.geteuid()} or stat.S_IMODE(info.st_mode) & 0o077:
        raise MonitorError(f"{label} must be private (0700) and owned by the supervisor/root")
    return path.resolve()


def install_protected_snapshot(path: Path, value: bytes, digest: str, mode: int, label: str) -> None:
    if path.exists() or path.is_symlink():
        existing, _ = capture_protected_file(path, label, digest)
        if existing != value:
            raise MonitorError(f"{label} differs from its authenticated source bytes")
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, mode)
    except FileExistsError:
        existing, _ = capture_protected_file(path, label, digest)
        if existing != value:
            raise MonitorError(f"{label} differs from its authenticated source bytes")
        return
    except OSError as exc:
        raise MonitorError(f"cannot create {label}: {exc}") from exc
    try:
        offset = 0
        while offset < len(value):
            written = os.write(descriptor, value[offset:])
            if written <= 0:
                raise MonitorError(f"cannot finish writing {label}")
            offset += written
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    capture_protected_file(path, label, digest)


def minimal_release_environment(config: dict) -> dict[str, str]:
    allowed: set[str] = {"TMPDIR", "LANG", "LC_ALL"}
    for field in (
        "planningEnvironmentAllowlist",
        "trackerEnvironmentAllowlist",
        "environmentAllowlist",
    ):
        names = config.get(field, [])
        if not isinstance(names, list) or not all(
            isinstance(name, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]*", name)
            for name in names
        ):
            raise MonitorError(f"deployment config {field} must be an array of environment names")
        allowed.update(names)
    forbidden = {
        "HOME", "BASH_ENV", "ENV", "PYTHONHOME", "PYTHONPATH", "NODE_OPTIONS",
        "RUBYOPT", "PERL5OPT",
    }
    unsafe = sorted(
        name for name in allowed
        if name in forbidden
        or name.startswith(("PYTHON", "LD_", "DYLD_", "BASH_FUNC_", "GIT_CONFIG"))
    )
    if unsafe:
        raise MonitorError(
            "deployment environment allowlists contain loader/control variables: "
            + ", ".join(unsafe)
        )
    child = {name: os.environ[name] for name in sorted(allowed) if name in os.environ}
    child["PATH"] = ACTIVE_TRUSTED_PATH
    child["PYTHONNOUSERSITE"] = "1"
    child["PYTHONSAFEPATH"] = "1"
    return child


def validate_supervisor_install(project: Path) -> Path:
    supervisor = Path(__file__).resolve()
    try:
        supervisor.relative_to(project)
    except ValueError:
        pass
    else:
        raise MonitorError(
            "autonomous cron requires pm-agent.py from a protected external installation outside the agent repository"
        )
    capture_protected_file(supervisor, "automation supervisor")
    interpreter = Path(sys.executable).resolve()
    try:
        interpreter.relative_to(project)
    except ValueError:
        pass
    else:
        raise MonitorError("automation supervisor Python must live outside the agent repository")
    capture_protected_file(interpreter, "automation supervisor Python")
    return interpreter


def load_protected_automation_config(path: Path) -> tuple[dict, Path]:
    candidate = path.expanduser()
    if not candidate.is_absolute() or candidate.is_symlink():
        raise MonitorError("automation config must be an absolute, non-symlink protected file")
    resolved = candidate.resolve()
    raw, _ = capture_protected_file(resolved, "automation config")
    try:
        config = strict_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MonitorError(f"automation config is invalid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise MonitorError("automation config must be a JSON object")
    return config, resolved


def bootstrap_automation() -> tuple[dict, Path, Path, Path]:
    """Authenticate scheduler inputs before consuming any config-controlled value."""
    config_path = Path(
        os.environ.get("STARTUP_FACTORY_AUTOMATION_CONFIG")
        or SKILL_DIR / "config" / "automation.config.json"
    )
    config, config_path = load_protected_automation_config(config_path)
    if config.get("schemaVersion") != 1:
        raise MonitorError("automation config schemaVersion must be 1")
    project = required_project_root()
    require_external_automation_config(config_path, project)
    configure_trusted_tools(config, project)
    verify_project_checkout(project)
    interpreter = validate_supervisor_install(project)
    return config, config_path, project, interpreter


def validate_release_deadline(config: dict) -> None:
    if config.get("mode") not in {"automatic", "approval-required"}:
        raise MonitorError("enabled deployment mode must be automatic or approval-required")
    defaults = {
        "plan": 300,
        "apply": 1800,
        "status": 120,
        "verify": 600,
        "rollback": 900,
        "verifyDelivery": 60,
        "verifyApproval": 60,
    }
    configured = config.get("timeoutsSeconds", {})
    if not isinstance(configured, dict):
        raise MonitorError("deployment timeoutsSeconds must be an object")
    values: dict[str, int] = {}
    for name, default in defaults.items():
        value = configured.get(name, default)
        if type(value) is not int or not 1 <= value <= 86400:
            raise MonitorError(f"deployment timeoutsSeconds.{name} must be an integer from 1 to 86400")
        values[name] = value
    attestor = "verifyDelivery" if config.get("mode") == "automatic" else "verifyApproval"
    # Worst recoverable pass: plan + authority attestor + pre/post status polls,
    # apply, independent verify, safe rollback, and bounded supervisor overhead.
    required = (
        values["plan"]
        + values[attestor]
        + (4 * values["status"])
        + values["apply"]
        + values["verify"]
        + values["rollback"]
        + 300
    )
    if RELEASE_TIMEOUT_SECONDS < required:
        raise MonitorError(
            f"releaseTimeoutSeconds must be at least {required} for the configured full release/rollback path"
        )


def validate_release_handoff(
    project: Path,
    *,
    dry_run: bool = False,
) -> tuple[list[str] | None, str | None, dict[str, str] | None]:
    """Authenticate and snapshot an external executor before its first instruction."""
    project = project.resolve()
    raw_config_path = os.environ.get("STARTUP_FACTORY_DEPLOYMENT_CONFIG")
    if not raw_config_path:
        # Repository-local defaults are documentation/templates, not a
        # production trust boundary. Do not execute any release program until
        # the scheduler supplies a protected external deployment config.
        return None, None, None
    config_path = Path(raw_config_path).expanduser()
    if not config_path.is_absolute() or config_path.is_symlink():
        raise MonitorError(
            "production handoff requires an absolute, non-symlink protected deployment config"
        )
    try:
        config_path.resolve().relative_to(project)
    except ValueError:
        pass
    else:
        raise MonitorError("production deployment config must live outside the agent repository")
    raw_config, config_digest = capture_protected_file(
        config_path, "deployment config"
    )
    try:
        config = strict_json(raw_config.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MonitorError(f"deployment config is invalid JSON: {exc}") from exc
    if not isinstance(config, dict) or config.get("schemaVersion") != 1:
        raise MonitorError("deployment config must be a schemaVersion 1 object")
    if type(config.get("enabled")) is not bool:
        raise MonitorError("deployment config enabled must be true or false")
    if not config["enabled"]:
        return None, str(config_path.resolve()), minimal_release_environment(config)
    validate_release_deadline(config)

    raw_state_root = config.get("stateRoot")
    if not isinstance(raw_state_root, str) or not raw_state_root:
        raise MonitorError("enabled production handoff requires an absolute stateRoot")
    state_root_path = Path(raw_state_root).expanduser()
    if not state_root_path.is_absolute() or state_root_path.is_symlink():
        raise MonitorError("stateRoot must be an absolute, non-symlink private directory")
    try:
        state_root_path.resolve().relative_to(project)
    except ValueError:
        pass
    else:
        raise MonitorError("stateRoot must live outside the agent repository")
    if state_root_path.exists():
        try:
            state_info = state_root_path.lstat()
        except OSError as exc:
            raise MonitorError(f"cannot inspect stateRoot: {exc}") from exc
        if stat.S_ISLNK(state_info.st_mode) or not stat.S_ISDIR(state_info.st_mode):
            raise MonitorError("stateRoot must be a non-symlink private directory")
        if state_info.st_uid not in {0, os.geteuid()} or stat.S_IMODE(state_info.st_mode) & 0o077:
            raise MonitorError("stateRoot must be private (0700) and owned by the supervisor/root")

    candidate = Path(
        tool_path("STARTUP_FACTORY_RELEASE_FEATURE", "release-feature.py")
    ).expanduser()
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file():
        raise MonitorError(
            "enabled production handoff requires STARTUP_FACTORY_RELEASE_FEATURE to name an absolute, non-symlink external executor"
        )
    resolved = candidate.resolve()
    try:
        resolved.relative_to(project)
    except ValueError:
        pass
    else:
        raise MonitorError(
            "enabled production handoff refuses the repository-local release executor; install the pinned skill outside the target project"
        )

    source_root = resolved.parent.parent
    if resolved != (source_root / RELEASE_SNAPSHOT_FILES["release-feature.py"]).resolve():
        raise MonitorError(
            "STARTUP_FACTORY_RELEASE_FEATURE must name bin/release-feature.py in the external skill install"
        )
    configured = config.get("trustedCodeDigests")
    if not isinstance(configured, dict) or set(configured) != set(RELEASE_SNAPSHOT_FILES):
        raise MonitorError(
            "trustedCodeDigests must contain the exact protected release helper set"
        )
    captured: dict[str, tuple[bytes, str]] = {}
    for name, relative in RELEASE_SNAPSHOT_FILES.items():
        expected_digest = configured.get(name)
        if not isinstance(expected_digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", expected_digest
        ):
            raise MonitorError(f"external release helper {name} needs a pinned sha256 digest")
        captured[name] = capture_protected_file(
            source_root / relative,
            f"external release helper {name}",
            expected_digest,
        )

    if dry_run:
        return (
            isolated_release_command(resolved.parent, resolved),
            str(config_path.resolve()),
            minimal_release_environment(config),
        )

    state_root = private_directory(state_root_path, "stateRoot")
    snapshot_parent = private_directory(
        state_root / "supervisor-entrypoints", "supervisor entrypoint directory"
    )
    snapshot = private_directory(
        snapshot_parent / config_digest.removeprefix("sha256:"),
        "authenticated release snapshot",
    )
    for directory in (snapshot / "bin", snapshot / "config"):
        private_directory(directory, "authenticated release snapshot directory")
    for name, relative in RELEASE_SNAPSHOT_FILES.items():
        value, digest = captured[name]
        executable = relative.suffix == ".sh" or name == "release-feature.py"
        install_protected_snapshot(
            snapshot / relative,
            value,
            digest,
            0o500 if executable else 0o400,
            f"authenticated release snapshot {name}",
        )
    snapshot_config = snapshot / "config" / "deployment.config.json"
    install_protected_snapshot(
        snapshot_config,
        raw_config,
        config_digest,
        0o400,
        "authenticated deployment config snapshot",
    )
    return (
        isolated_release_command(
            snapshot / "bin",
            snapshot / RELEASE_SNAPSHOT_FILES["release-feature.py"],
        ),
        str(snapshot_config),
        minimal_release_environment(config),
    )


def resolve_scan_statuses(automation: dict) -> list[str]:
    board = load_json(SKILL_DIR / "config" / "statuses.config.json", "status config")
    kinds = automation.get("scanStatusKinds")
    if not isinstance(kinds, list) or not kinds:
        raise MonitorError("scanStatusKinds must be a non-empty list")
    by_kind: dict[str, list[str]] = defaultdict(list)
    for status in board.get("tasks", {}).get("statuses", []):
        if status.get("kind"):
            by_kind[str(status["kind"])].append(str(status.get("name")))
    names: list[str] = []
    for kind in kinds:
        matches = by_kind.get(str(kind), [])
        if len(matches) != 1:
            raise MonitorError(f"status kind '{kind}' must resolve to exactly one [task] status (found {matches})")
        names.append(matches[0])
    return names


def feature_status_for_kind(kind: str) -> str:
    board = load_json(SKILL_DIR / "config" / "statuses.config.json", "status config")
    matches = [
        str(status.get("name"))
        for status in board.get("features", {}).get("statuses", [])
        if status.get("kind") == kind and not status.get("terminal")
    ]
    if len(matches) != 1:
        raise MonitorError(
            f"feature status kind '{kind}' must resolve to exactly one non-terminal status (found {matches})"
        )
    return matches[0]


def reopen_feature(
    project: Path,
    env: dict[str, str],
    feature_id: str,
    *,
    dry_run: bool,
) -> str:
    target = feature_status_for_kind("queued")
    if dry_run:
        print(
            f"plan: would reopen terminal feature {safe_log_value(feature_id)} to [{target}] "
            "before generation dispatch"
        )
        return target
    broker_env = dict(env)
    broker_env["STARTUP_FACTORY_PM_SUPERVISOR"] = "1"
    run(
        [
            tool_path("STARTUP_FACTORY_TRACKER_OPS", "tracker-ops.sh"),
            "feature-reopen",
            feature_id,
            target,
        ],
        cwd=project,
        env=broker_env,
        label=f"feature reopen for {safe_log_value(feature_id)}",
    )
    return target


def revision_key(raw: object) -> tuple[int, object] | None:
    """Return a comparable chronological/revision key, or None if ordering is unsafe."""
    if raw is None or isinstance(raw, bool):
        return None
    value = str(raw).strip()
    if not value:
        return None
    try:
        return (1, int(value))
    except ValueError:
        pass
    markdown_offset = re.fullmatch(r"markdown-offset:(\d+)", value)
    if markdown_offset:
        return (1, int(markdown_offset.group(1)))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (2, parsed.timestamp())
    except ValueError:
        return None


def latest_metadata(items: list[dict], key: str) -> tuple[str | None, str | None]:
    """Resolve the newest explicit metadata occurrence without trusting scan order."""
    pattern = re.compile(
        r"^\s*" + re.escape(key) + r"\s*:\s*([^\r\n#]+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    occurrences: list[tuple[tuple[int, object], str]] = []
    unordered = False
    for item in items:
        # A tracker issue's updatedAt normally changes for status/assignee edits,
        # so it cannot prove when description metadata itself was authored.
        # Treat descriptions as the baseline. Subsequent routing changes belong
        # in timestamped/revisioned comments, which every unattended adapter
        # must export exhaustively.
        sources: list[tuple[str, object]] = [
            (str(item.get("description") or ""), "baseline")
        ]
        for comment in item.get("comments") or []:
            if not isinstance(comment, dict):
                continue
            sources.append(
                (
                    str(comment.get("body") or ""),
                    comment.get("updatedAt")
                    or comment.get("createdAt")
                    or comment.get("revision"),
                )
            )
        for text, raw_revision in sources:
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            order = (0, 0) if raw_revision == "baseline" else revision_key(raw_revision)
            if order is None:
                unordered = True
            else:
                # Array and line order are serialization details, not evidence
                # that one value superseded another at the same revision. Keep
                # every value so a tied contradiction fails closed. Repeated
                # identical values remain harmless.
                occurrences.extend(
                    (order, match.group(1).strip().lower()) for match in matches
                )
    if unordered:
        return None, f"{key} metadata lacks a sortable timestamp/revision"
    if not occurrences:
        return None, None
    newest = max(order for order, _ in occurrences)
    newest_values = {value for order, value in occurrences if order == newest}
    if len(newest_values) != 1:
        return None, f"conflicting latest {key} metadata: " + ", ".join(sorted(newest_values))
    return next(iter(newest_values)), None


def route(items: list[dict], config: dict) -> tuple[str | None, str | None]:
    metadata = config.get("metadata") or {}
    opt_key = str(metadata.get("optInKey") or "automation")
    preset_key = str(metadata.get("teamPresetKey") or "team-preset")
    opt_value, opt_error = latest_metadata(items, opt_key)
    if opt_error:
        return None, opt_error
    if opt_value not in {None, "enabled", "disabled"}:
        return None, f"unknown {opt_key} metadata value: {opt_value} (accepted: enabled, disabled)"
    if opt_value == "disabled":
        return None, "automation disabled by [task] metadata"
    if config.get("requireMetadataOptIn") and opt_value != "enabled":
        return None, "automation opt-in metadata is required"
    preset, preset_error = latest_metadata(items, preset_key)
    if preset_error:
        return None, preset_error
    allowed = set(config.get("allowedTeamPresets") or [])
    preset = preset or str(config.get("defaultTeamPreset") or "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", preset or "") or preset not in allowed:
        return None, f"unknown or disallowed team preset: {preset or '<empty>'}"
    if not (SKILL_DIR / "teams" / f"{preset}.md").is_file():
        return None, f"team preset has no definition: {preset}"
    return preset, None


def route_failure_needs_escalation(reason: str) -> bool:
    return reason not in {
        "automation disabled by [task] metadata",
        "automation opt-in metadata is required",
    }


def pause_registered_run(entry: dict, reason: str, *, out_of_scope: bool = False) -> None:
    """Persist an execution pause without losing the state to resume from."""
    state = str(entry.get("state") or "discovered")
    if state != "deployed" and state != "paused":
        entry["resumeState"] = state
        entry["state"] = "paused"
        entry["pausedAt"] = iso_now()
    entry["eligibility"] = "out-of-scope" if out_of_scope else "paused"
    entry["pauseReason"] = safe_log_value(reason, 240)
    entry["eligibilityCheckedAt"] = iso_now()


def resume_registered_run(entry: dict) -> None:
    """Resume only when a fresh scan proves the original immutable route eligible."""
    entry["eligibility"] = "eligible"
    entry["eligibilityCheckedAt"] = iso_now()
    entry.pop("pauseReason", None)
    if entry.get("state") == "paused":
        entry["state"] = str(entry.pop("resumeState", None) or "discovered")
        entry["resumedAt"] = iso_now()


def safe_run_identity(
    feature_id: str, title: str | None, prefix: str, generation: int = 1
) -> tuple[str, str]:
    if type(generation) is not int or generation < 1:
        raise MonitorError("generation must be a positive integer")
    digest = hashlib.sha256(feature_id.encode()).hexdigest()
    source = title or "feature"
    slug = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")[:24] or "feature"
    clean_prefix = re.sub(r"[^a-z0-9-]+", "-", prefix.lower()).strip("-")[:20].rstrip("-")
    if not clean_prefix:
        clean_prefix = "factory"
    base_team = f"{clean_prefix}-{slug}-{digest[:10]}"[:63].rstrip("-")
    if generation == 1:
        run_id = digest[:20]
        team = base_team
    else:
        run_id = hashlib.sha256(
            (feature_id + "\0generation:" + str(generation)).encode()
        ).hexdigest()[:20]
        suffix = f"-g{generation}"
        team = base_team[: 63 - len(suffix)].rstrip("-") + suffix
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", team):
        raise MonitorError("generated an unsafe team identifier")
    return run_id, team


def safe_log_value(value: object, limit: int = 160) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()[:limit] or "<empty>"


def escalation_body(reason: str) -> str:
    reason = safe_log_value(reason, 240)
    return (
        "[escalation]\n"
        "question: Which configured team preset should own this [feature]?\n"
        f"context: Portfolio automation stopped before launch because {reason}.\n"
        "options:\n"
        "- Add one valid team-preset metadata line; the next pass will launch it.\n"
        "- Set automation: disabled; the supervisor will leave the [feature] untouched.\n"
        "default-if-silent: do not execute; remain blocked\n\n"
        "— pm-agent"
    )


def failure_escalation_body(kind: str) -> str:
    # Intentionally omit raw exceptions, paths, command output, and identifiers.
    return (
        "[escalation]\n"
        "question: Should the team lead retry after inspecting the protected supervisor logs?\n"
        f"context: {safe_log_value(kind, 120)}. No production action was authorized by this comment.\n"
        "options:\n"
        "- Correct the configuration or failed gate, then let the next supervisor pass retry.\n"
        "- Set automation: disabled while a human investigates.\n"
        "default-if-silent: do not bypass the failed gate; remain blocked\n\n"
        "— pm-agent"
    )


def post_once(task_id: str, delivery_id: str, body: str, *, root: Path, env: dict[str, str]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(body)
        body_path = Path(handle.name)
    try:
        run(
            [tool_path("STARTUP_FACTORY_TRACKER_OPS", "tracker-ops.sh"), "comment-once", task_id, delivery_id, str(body_path)],
            cwd=root,
            env=env,
            label=f"escalation on {safe_log_value(task_id)}",
        )
    finally:
        body_path.unlink(missing_ok=True)


def escalate_routing_failure(
    feature_id: str,
    items: list[dict],
    reason: str,
    *,
    root: Path,
    env: dict[str, str],
    dry_run: bool,
) -> None:
    if not route_failure_needs_escalation(reason):
        return
    if dry_run:
        print(f"plan: would post routing escalation for feature {safe_log_value(feature_id)}")
        return
    task_id = min(
        (str(item.get("taskId")) for item in items if item.get("taskId")),
        default="",
    )
    if not task_id:
        print("pm-agent: cannot escalate routing failure without a taskId", file=sys.stderr)
        return
    delivery = "pm-route-" + hashlib.sha256((feature_id + reason).encode()).hexdigest()[:20]
    post_once(task_id, delivery, escalation_body(reason), root=root, env=env)


def resolve_base_commit(project: Path, base_ref: str, env: dict[str, str]) -> str:
    commit = run(
        git_argv("rev-parse", "--verify", f"{base_ref}^{{commit}}"),
        cwd=project,
        env=unprivileged_git_environment(env),
        label="base ref lookup",
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise MonitorError("base ref did not resolve to a full commit hash")
    return commit


def filesystem_identity(path: Path, label: str) -> str:
    try:
        info = path.lstat()
    except OSError as exc:
        raise MonitorError(f"cannot inspect {label}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise MonitorError(f"{label} must be a non-symlink directory")
    return f"{info.st_dev}:{info.st_ino}"


def git_is_ancestor(project: Path, ancestor: str, descendant: str, env: dict[str, str], label: str) -> bool:
    try:
        result = subprocess.run(
            git_argv("merge-base", "--is-ancestor", ancestor, descendant),
            cwd=project,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise MonitorError(f"{label} exceeded its operation deadline") from exc
    if result.returncode not in {0, 1}:
        raise MonitorError(f"{label} failed")
    return result.returncode == 0


def ensure_worktree(
    project: Path,
    run_path: Path,
    team: str,
    base_ref: str,
    base_commit: str,
    env: dict[str, str],
    *,
    start_commit: str | None = None,
    predecessor_team: str | None = None,
) -> dict[str, str]:
    git_env = unprivileged_git_environment(env)
    if not re.fullmatch(r"[0-9a-f]{40}", base_commit):
        raise MonitorError("registered run has an invalid immutable base commit")
    base_tip = resolve_base_commit(project, base_ref, env)
    if not git_is_ancestor(
        project, base_commit, base_tip, git_env, "base ancestry check"
    ):
        raise MonitorError("trusted base ref no longer contains the registered immutable base commit")
    start_commit = start_commit or base_commit
    if not re.fullmatch(r"[0-9a-f]{40}", start_commit):
        raise MonitorError("registered run has an invalid immutable start commit")
    if not git_is_ancestor(
        project, base_commit, start_commit, git_env, "generation ancestry check"
    ):
        raise MonitorError("generation start commit does not descend from the registered immutable base commit")
    if predecessor_team is not None and not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{0,62}", predecessor_team
    ):
        raise MonitorError("registered run has an unsafe predecessor team identifier")
    try:
        branch = subprocess.run(
            git_argv("show-ref", "--verify", "--quiet", f"refs/heads/{team}"),
            cwd=project, env=git_env,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise MonitorError("feature branch lookup exceeded its operation deadline") from exc
    if run_path.exists() and branch.returncode:
        if not predecessor_team:
            raise MonitorError(
                f"pre-existing integration workspace {run_path} has no source feature branch '{team}'"
            )
        observed_branch = run(
            git_argv("branch", "--show-current"), cwd=run_path, env=git_env,
            label="predecessor integration worktree branch check",
        ).stdout.strip()
        observed_commit = run(
            git_argv("rev-parse", "--verify", "HEAD^{commit}"), cwd=run_path, env=git_env,
            label="predecessor integration worktree commit check",
        ).stdout.strip()
        dirty = run(
            git_argv("status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"),
            cwd=run_path,
            env=git_env,
            label="predecessor integration worktree clean check",
        ).stdout.strip()
        if observed_branch != predecessor_team or observed_commit != start_commit or dirty:
            raise MonitorError(
                "generation workspace cannot rotate: predecessor branch/commit/cleanliness does not match the deployed run"
            )
        run(
            git_argv("switch", "-c", team, start_commit),
            cwd=run_path,
            env=git_env,
            label="generation feature branch creation",
        )
        branch = subprocess.CompletedProcess([], 0)
    if not run_path.exists() and not branch.returncode:
        raise MonitorError(
            f"pre-existing unregistered feature branch '{team}' cannot seed an autonomous run"
        )
    if branch.returncode:
        run(
            git_argv("branch", team, start_commit),
            cwd=project,
            env=git_env,
            label="feature branch creation",
        )
    expected_commit = run(
        git_argv("rev-parse", "--verify", f"refs/heads/{team}^{{commit}}"),
        cwd=project, env=git_env, label="feature branch commit lookup",
    ).stdout.strip()
    if not git_is_ancestor(
        project, base_commit, expected_commit, git_env, "feature ancestry check"
    ):
        raise MonitorError("feature branch does not descend from the registered immutable base commit")
    if not git_is_ancestor(
        project, start_commit, expected_commit, git_env, "generation start ancestry check"
    ):
        raise MonitorError("feature branch does not descend from its immutable generation start commit")

    if not run_path.exists():
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run(
            git_argv("worktree", "add", str(run_path), team),
            cwd=project, env=git_env, label="integration worktree creation",
        )

    project_common_raw = run(
        git_argv("rev-parse", "--git-common-dir"), cwd=project, env=git_env,
        label="project Git common-directory lookup",
    ).stdout.strip()
    run_common_raw = run(
        git_argv("rev-parse", "--git-common-dir"), cwd=run_path, env=git_env,
        label="integration worktree common-directory lookup",
    ).stdout.strip()
    project_common = Path(project_common_raw)
    if not project_common.is_absolute():
        project_common = project / project_common
    run_common = Path(run_common_raw)
    if not run_common.is_absolute():
        run_common = run_path / run_common
    if project_common.resolve() != run_common.resolve():
        raise MonitorError(
            f"integration worktree {run_path} does not belong to the project Git common directory"
        )
    git_dir_raw = run(
        git_argv("rev-parse", "--git-dir"), cwd=run_path, env=git_env,
        label="integration worktree Git-directory lookup",
    ).stdout.strip()
    git_dir = Path(git_dir_raw)
    if not git_dir.is_absolute():
        git_dir = run_path / git_dir
    git_dir = git_dir.resolve()

    observed_root = Path(
        run(
            git_argv("rev-parse", "--show-toplevel"), cwd=run_path, env=git_env,
            label="integration worktree root lookup",
        ).stdout.strip()
    ).resolve()
    if observed_root != run_path.resolve():
        raise MonitorError(
            f"integration worktree {run_path} resolves to a different Git worktree root"
        )
    observed_branch = run(
        git_argv("branch", "--show-current"), cwd=run_path, env=git_env,
        label="integration worktree branch check",
    ).stdout.strip()
    if observed_branch != team:
        raise MonitorError(
            f"integration worktree {run_path} is on '{observed_branch}', expected '{team}'"
        )
    observed_commit = run(
        git_argv("rev-parse", "--verify", "HEAD^{commit}"), cwd=run_path, env=git_env,
        label="integration worktree commit check",
    ).stdout.strip()
    if observed_commit != expected_commit:
        raise MonitorError(
            f"integration worktree {run_path} HEAD does not match the project feature branch"
        )

    try:
        registry = subprocess.run(
            git_argv("worktree", "list", "--porcelain", "-z"),
            cwd=project,
            env=git_env,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise MonitorError(
            "project worktree registration lookup exceeded its operation deadline"
        ) from exc
    if registry.returncode:
        raise MonitorError("project worktree registration lookup failed")
    records: list[dict[str, str]] = []
    for block in registry.stdout.split("\0\0"):
        record: dict[str, str] = {}
        for field in block.split("\0"):
            key, separator, value = field.partition(" ")
            if separator and key in {"worktree", "HEAD", "branch"}:
                record[key] = value
        if record:
            records.append(record)
    matching = [
        record for record in records
        if record.get("worktree")
        and Path(record["worktree"]).resolve() == run_path.resolve()
    ]
    expected_branch = f"refs/heads/{team}"
    if (
        len(matching) != 1
        or matching[0].get("branch") != expected_branch
        or matching[0].get("HEAD") != expected_commit
    ):
        raise MonitorError(
            f"integration worktree {run_path} is not registered to {expected_branch} at the expected commit"
        )
    common_dir = run_common.resolve()
    return {
        "gitDir": str(git_dir),
        "gitDirId": filesystem_identity(git_dir, "integration worktree Git directory"),
        "gitCommonDir": str(common_dir),
        "gitCommonDirId": filesystem_identity(common_dir, "project Git common directory"),
        "head": expected_commit,
    }


def terminal_task_names() -> set[str]:
    board = load_json(SKILL_DIR / "config" / "statuses.config.json", "status config")
    return {
        str(status["name"])
        for status in board.get("tasks", {}).get("statuses", [])
        if status.get("terminal") and status.get("requiresCommit")
    }


def all_integrated(snapshot: Path) -> bool:
    try:
        payload = load_json(snapshot, "tracker snapshot")
    except MonitorError:
        return False
    tasks = payload.get("tasks") or []
    terminal = terminal_task_names()
    return bool(tasks) and bool(terminal) and all(task.get("status") in terminal for task in tasks)


def reconcile_run(
    entry: dict,
    *,
    project: Path,
    automation_root: Path,
    env: dict[str, str],
    release_command: list[str] | None,
    deployment_config: str | None,
    release_environment: dict[str, str] | None,
    dry_run: bool,
) -> None:
    if entry.get("state") == "paused" or entry.get("eligibility") != "eligible":
        raise MonitorError("refusing to reconcile a paused or out-of-scope registered run")
    feature_id = str(entry.get("featureId") or "")
    team = str(entry.get("team") or "")
    preset = str(entry.get("preset") or "")
    base_ref = str(entry.get("baseRef") or "")
    base_commit = str(entry.get("baseCommit") or "")
    start_commit = str(entry.get("startCommit") or base_commit)
    if not feature_id:
        raise MonitorError("registered run has no feature identifier")
    run_id = str(entry.get("runId") or "")
    if not re.fullmatch(r"[a-f0-9]{20}", run_id):
        raise MonitorError("registered run has an unsafe run identifier")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", team):
        raise MonitorError("registered run has an unsafe team identifier")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", preset):
        raise MonitorError("registered run has an unsafe preset identifier")
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", base_ref)
        or ".." in base_ref
        or "//" in base_ref
        or base_ref.endswith(("/", ".", ".lock"))
    ):
        raise MonitorError("registered run has an unsafe base ref")
    if not re.fullmatch(r"[0-9a-f]{40}", base_commit):
        raise MonitorError("registered run has no valid immutable baseCommit")
    if not re.fullmatch(r"[0-9a-f]{40}", start_commit):
        raise MonitorError("registered run has no valid immutable startCommit")
    workspace_id = str(entry.get("workspaceId") or run_id)
    if not re.fullmatch(r"[a-f0-9]{20}", workspace_id):
        raise MonitorError("registered run has an unsafe workspace identifier")
    expected_repo = managed_path(
        automation_root, "runs", workspace_id, "repo", label="registered run workspace"
    )
    repo = Path(str(entry.get("repository") or "")).expanduser().resolve()
    if repo != expected_repo:
        raise MonitorError("registered run repository does not match its derived isolated workspace")
    if dry_run:
        print(f"plan: reconcile {safe_log_value(feature_id)} with {preset} as {team} in {repo}")
        return
    ensure_worktree(
        project,
        repo,
        team,
        base_ref,
        base_commit,
        env,
        start_commit=start_commit,
        predecessor_team=str(entry.get("predecessorTeam") or "") or None,
    )
    if not entry.get("launchedAt"):
        run(
            [tool_path("STARTUP_FACTORY_LAUNCH_TEAM", "launch-team.sh"), "gate-team", preset, team, feature_id],
            cwd=repo,
            env=env,
            label=f"team launch for {safe_log_value(feature_id)}",
        )
        entry["launchedAt"] = iso_now()
        entry["state"] = "running"
    run(
        [tool_path("STARTUP_FACTORY_DISPATCH", "dispatch.sh"), team, feature_id, "--once"],
        cwd=repo,
        env=env,
        label=f"dispatch for {safe_log_value(feature_id)}",
    )
    teamwork = read_teamwork_root()
    team_workspace = repo / teamwork / team
    if not all_integrated(team_workspace / "tasks.json"):
        entry["state"] = "running"
        entry["lastReconciledAt"] = iso_now()
        return
    entry["state"] = "ready-to-deploy"
    if release_command is None:
        entry["state"] = "awaiting-deployment"
        entry["lastReconciledAt"] = iso_now()
        return
    # Dispatch and integration operate on an agent-writable checkout. Bind the
    # release handoff to the same registered Git directories immediately before
    # the protected executor starts; the executor ignores later .git-pointer swaps.
    git_identity = ensure_worktree(
        project,
        repo,
        team,
        base_ref,
        base_commit,
        env,
        start_commit=start_commit,
        predecessor_team=str(entry.get("predecessorTeam") or "") or None,
    )
    release_argv = [
        *release_command,
        "--repository", str(repo),
        "--workspace", str(team_workspace),
        "--team", team,
        "--feature", feature_id,
        "--expected-git-dir", git_identity["gitDir"],
        "--expected-git-dir-id", git_identity["gitDirId"],
        "--expected-git-common-dir", git_identity["gitCommonDir"],
        "--expected-git-common-dir-id", git_identity["gitCommonDirId"],
    ]
    if deployment_config:
        release_argv += ["--config", deployment_config]
    release = run(
        release_argv,
        cwd=repo,
        env=release_environment or env,
        label=f"production release for {safe_log_value(feature_id)}",
        check=False,
        timeout_seconds=RELEASE_TIMEOUT_SECONDS,
    )
    if release.returncode == 0:
        entry["state"] = "deployed"
        entry["deployedAt"] = iso_now()
    elif release.returncode == 4:
        entry["state"] = "awaiting-deployment"
    else:
        entry["state"] = "deployment-blocked"
        entry["lastReconciledAt"] = iso_now()
        raise MonitorError(
            f"production release for {safe_log_value(feature_id)} stopped with exit {release.returncode}"
        )
    entry["lastReconciledAt"] = iso_now()


def scan_board(project: Path, env: dict[str, str], statuses: list[str], outfile: Path) -> dict:
    argv = [tool_path("STARTUP_FACTORY_TRACKER_OPS", "tracker-ops.sh"), "scan", str(outfile)]
    for status in statuses:
        argv += ["--status", status]
    run(argv, cwd=project, env=env, label="portfolio scan")
    payload = load_json(outfile, "portfolio scan")
    if payload.get("schemaVersion") != 1 or not isinstance(payload.get("items"), list):
        raise MonitorError("tracker scan returned an unsupported schema")
    return payload


def export_registered_feature(project: Path, env: dict[str, str], feature_id: str, outfile: Path) -> list[dict]:
    run(
        [tool_path("STARTUP_FACTORY_TRACKER_OPS", "tracker-ops.sh"), "export", feature_id, str(outfile)],
        cwd=project,
        env=env,
        label=f"registered feature authorization snapshot for {safe_log_value(feature_id)}",
    )
    payload = load_json(outfile, "registered feature authorization snapshot")
    if str(payload.get("featureId") or "") != feature_id or not isinstance(payload.get("tasks"), list):
        raise MonitorError("registered feature authorization snapshot has an unsupported schema")
    return payload["tasks"]


def one_pass(*, dry_run: bool) -> int:
    global COMMAND_TIMEOUT_SECONDS, RELEASE_TIMEOUT_SECONDS
    config, config_path, project, _interpreter = bootstrap_automation()
    timeout = config.get("operationTimeoutSeconds", 120)
    if type(timeout) is not int or not 5 <= timeout <= 3600:
        raise MonitorError("operationTimeoutSeconds must be an integer from 5 to 3600")
    COMMAND_TIMEOUT_SECONDS = timeout
    release_timeout = config.get("releaseTimeoutSeconds", 7200)
    if type(release_timeout) is not int or not 60 <= release_timeout <= 86400:
        raise MonitorError("releaseTimeoutSeconds must be an integer from 60 to 86400")
    RELEASE_TIMEOUT_SECONDS = release_timeout
    enabled = config.get("enabled")
    if type(enabled) is not bool:
        raise MonitorError("enabled must be true or false")
    if not enabled and not dry_run:
        print("pm-agent: disabled (set enabled=true in config/automation.config.json)")
        return 0
    validate_pm_automation(project)
    lifecycle_root = validate_team_safety(config, project)
    release_command, deployment_config, release_environment = validate_release_handoff(
        project, dry_run=dry_run
    )
    automation_root = contained(project, str(config.get("workspaceRoot") or ""), "workspaceRoot")
    env = supervisor_child_environment()
    env["TRACKER_PROJECT_ROOT"] = str(project)
    env["STARTUP_FACTORY_LIFECYCLE_STATE_ROOT"] = str(lifecycle_root)
    lease = Lease(
        managed_path(automation_root, "monitor.lock", label="monitor lease"),
        integer_setting(config, "leaseSeconds", 900, 5, 86400),
    )
    if not dry_run and not lease.acquire():
        print("pm-agent: another live pass owns the monitor lease; skipping")
        return 0
    try:
        statuses = resolve_scan_statuses(config)
        if dry_run:
            with tempfile.TemporaryDirectory(prefix="startup-factory-scan-") as temp:
                scan = scan_board(project, env, statuses, Path(temp) / "scan.json")
        else:
            automation_root.mkdir(parents=True, exist_ok=True)
            scan = scan_board(
                project,
                env,
                statuses,
                managed_path(automation_root, "last-scan.json", label="portfolio scan snapshot"),
            )

        registry_path = managed_path(automation_root, "state.json", label="run registry")
        if registry_path.exists():
            registry = load_json(registry_path, "run registry")
        else:
            registry = {"schemaVersion": 1, "features": {}}
        if registry.get("schemaVersion") != 1 or not isinstance(registry.get("features"), dict):
            raise MonitorError("run registry has an unsupported schema")
        reconcile_registered = config.get("reconcileRegisteredRuns", True)
        if not isinstance(reconcile_registered, bool):
            raise MonitorError("reconcileRegisteredRuns must be true or false")

        groups: dict[str, list[dict]] = defaultdict(list)
        for item in scan.get("items") or []:
            feature_id = item.get("featureId")
            if feature_id is None:
                continue
            groups[str(feature_id)].append(item)

        for orphan in scan.get("orphans") or []:
            task_id = str(orphan.get("taskId") or "")
            if not task_id:
                print("pm-agent: ignored malformed orphan without taskId", file=sys.stderr)
                continue
            if dry_run:
                print(f"plan: would quarantine and escalate orphan task {safe_log_value(task_id)}")
            else:
                delivery = "pm-orphan-" + hashlib.sha256(task_id.encode()).hexdigest()[:20]
                post_once(
                    task_id,
                    delivery,
                    escalation_body("the [task] has no parent [feature]"),
                    root=project,
                    env=env,
                )

        features: dict[str, dict] = registry["features"]
        for registry_feature_id, entry in features.items():
            if not isinstance(entry, dict):
                raise MonitorError(f"run registry entry {safe_log_value(registry_feature_id)} must be an object")
            if str(entry.get("featureId") or "") != str(registry_feature_id):
                raise MonitorError(
                    f"run registry key {safe_log_value(registry_feature_id)} does not match its featureId"
                )
            generation = entry.get("generation", 1)
            if type(generation) is not int or generation < 1:
                raise MonitorError(
                    f"run registry entry {safe_log_value(registry_feature_id)} has an invalid generation"
                )
            history = entry.get("history", [])
            if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
                raise MonitorError(
                    f"run registry entry {safe_log_value(registry_feature_id)} has invalid generation history"
                )
            if not re.fullmatch(r"[0-9a-f]{40}", str(entry.get("baseCommit") or "")):
                raise MonitorError(
                    f"run registry entry {safe_log_value(registry_feature_id)} has no valid immutable baseCommit"
                )
            if not re.fullmatch(
                r"[0-9a-f]{40}",
                str(entry.get("startCommit") or entry.get("baseCommit") or ""),
            ):
                raise MonitorError(
                    f"run registry entry {safe_log_value(registry_feature_id)} has no valid immutable startCommit"
                )
            if not re.fullmatch(
                r"[0-9a-f]{20}",
                str(entry.get("workspaceId") or entry.get("runId") or ""),
            ):
                raise MonitorError(
                    f"run registry entry {safe_log_value(registry_feature_id)} has an invalid workspaceId"
                )
        registered_at_start = set(features)

        # A durable registration is not a durable authorization. Re-evaluate
        # every unfinished run from an exhaustive feature export before dispatch
        # or release. The queued/blocked scan is discovery-only: an active run
        # must not be paused merely because its tasks progressed to working/review.
        for feature_id in sorted(registered_at_start):
            entry = features[feature_id]
            if entry.get("state") == "deployed":
                continue
            temporary_authorization = False
            if dry_run:
                handle = tempfile.NamedTemporaryFile(prefix="startup-factory-auth-", suffix=".json", delete=False)
                handle.close()
                authorization_path = Path(handle.name)
                temporary_authorization = True
            else:
                authorization_dir = managed_path(
                    automation_root, "authorizations", label="authorization snapshots"
                )
                authorization_dir.mkdir(parents=True, exist_ok=True)
                authorization_path = managed_path(
                    authorization_dir,
                    f"{entry.get('runId')}.json",
                    label="authorization snapshot",
                )
            try:
                items = export_registered_feature(project, env, feature_id, authorization_path)
            except MonitorError as exc:
                reason = "the [feature] cannot be read from the authoritative tracker scope"
                pause_registered_run(entry, reason, out_of_scope=True)
                entry["authorizationError"] = str(exc)
                print(f"pm-agent: paused {safe_log_value(feature_id)}: {reason}")
                continue
            finally:
                if temporary_authorization:
                    authorization_path.unlink(missing_ok=True)
            if not items:
                reason = "the authoritative [feature] export contains no tasks"
                pause_registered_run(entry, reason, out_of_scope=True)
                print(f"pm-agent: paused {safe_log_value(feature_id)}: {reason}")
                continue
            observed_preset, reason = route(items, config)
            if reason:
                pause_registered_run(entry, reason)
                print(f"pm-agent: paused {safe_log_value(feature_id)}: {safe_log_value(reason)}")
                if entry.get("state") != "deployed":
                    try:
                        escalate_routing_failure(
                            feature_id,
                            items,
                            reason,
                            root=project,
                            env=env,
                            dry_run=dry_run,
                        )
                    except MonitorError as exc:
                        entry["trackerEscalationError"] = str(exc)
                continue
            if observed_preset != entry.get("preset"):
                reason = (
                    f"team preset changed from {entry.get('preset') or '<empty>'} "
                    f"to {observed_preset or '<empty>'}; the existing run cannot be rerouted in place"
                )
                pause_registered_run(entry, reason)
                print(f"pm-agent: paused {safe_log_value(feature_id)}: {safe_log_value(reason)}")
                if entry.get("state") != "deployed":
                    try:
                        escalate_routing_failure(
                            feature_id,
                            items,
                            reason,
                            root=project,
                            env=env,
                            dry_run=dry_run,
                        )
                    except MonitorError as exc:
                        entry["trackerEscalationError"] = str(exc)
                continue
            resume_registered_run(entry)
            entry["escalationTaskId"] = min(
                (str(item.get("taskId")) for item in items if item.get("taskId")),
                default=str(entry.get("escalationTaskId") or ""),
            )

        new_feature_ids: set[str] = set()
        new_count = 0
        limit = integer_setting(config, "maxFeaturesPerPass", 1, 1, 1000)
        for feature_id in sorted(groups):
            previous = features.get(feature_id)
            if previous is not None and previous.get("state") != "deployed":
                continue
            if new_count >= limit:
                print(f"pm-agent: cold-start limit reached; deferring {feature_id}")
                continue
            discovery_items = groups[feature_id]
            title = next(
                (str(item.get("featureTitle")) for item in discovery_items if item.get("featureTitle")),
                None,
            )
            generation = int(previous.get("generation") or 1) + 1 if previous else 1
            run_key, team = safe_run_identity(
                feature_id,
                title,
                str(config.get("branchPrefix") or "factory"),
                generation,
            )
            temporary_authorization = False
            if dry_run:
                handle = tempfile.NamedTemporaryFile(
                    prefix="startup-factory-discovery-auth-", suffix=".json", delete=False
                )
                handle.close()
                authorization_path = Path(handle.name)
                temporary_authorization = True
            else:
                authorization_dir = managed_path(
                    automation_root, "authorizations", label="authorization snapshots"
                )
                authorization_dir.mkdir(parents=True, exist_ok=True)
                authorization_path = managed_path(
                    authorization_dir, f"{run_key}.json", label="new-run authorization snapshot"
                )
            try:
                items = export_registered_feature(project, env, feature_id, authorization_path)
            except MonitorError as exc:
                print(
                    f"pm-agent: {safe_log_value(feature_id)} not launched: "
                    "the complete authoritative [feature] export could not be verified",
                    file=sys.stderr,
                )
                if not dry_run:
                    try:
                        escalate_routing_failure(
                            feature_id,
                            discovery_items,
                            "the complete authoritative [feature] export could not be verified",
                            root=project,
                            env=env,
                            dry_run=False,
                        )
                    except MonitorError:
                        pass
                continue
            finally:
                if temporary_authorization:
                    authorization_path.unlink(missing_ok=True)
            if not items:
                print(
                    f"pm-agent: {safe_log_value(feature_id)} not launched: "
                    "the complete authoritative [feature] export contains no tasks"
                )
                continue
            preset, reason = route(items, config)
            if reason:
                print(f"pm-agent: {safe_log_value(feature_id)} not launched: {safe_log_value(reason)}")
                escalate_routing_failure(
                    feature_id,
                    items,
                    reason,
                    root=project,
                    env=env,
                    dry_run=dry_run,
                )
                continue
            if previous and preset != previous.get("preset"):
                reason = (
                    f"team preset changed from {previous.get('preset') or '<empty>'} "
                    f"to {preset or '<empty>'}; a deployed evidence chain cannot be rerouted in place"
                )
                print(f"pm-agent: {safe_log_value(feature_id)} not reopened: {safe_log_value(reason)}")
                escalate_routing_failure(
                    feature_id,
                    items,
                    reason,
                    root=project,
                    env=env,
                    dry_run=dry_run,
                )
                continue
            workspace_id = (
                str(previous.get("workspaceId") or previous.get("runId") or "")
                if previous
                else run_key
            )
            if not re.fullmatch(r"[0-9a-f]{20}", workspace_id):
                raise MonitorError(
                    f"cannot reopen {safe_log_value(feature_id)} without its stable workspaceId"
                )
            repo = managed_path(
                automation_root, "runs", workspace_id, "repo", label="new run workspace"
            )
            base_ref = (
                str(previous.get("baseRef") or config.get("baseRef") or "main")
                if previous else str(config.get("baseRef") or "main")
            )
            base_commit = (
                str(previous.get("baseCommit") or "")
                if previous else resolve_base_commit(project, base_ref, env)
            )
            if not re.fullmatch(r"[0-9a-f]{40}", base_commit):
                raise MonitorError(
                    f"cannot reopen {safe_log_value(feature_id)} without its immutable baseCommit"
                )
            predecessor_team = str(previous.get("team") or "") if previous else ""
            if previous:
                predecessor_identity = ensure_worktree(
                    project,
                    repo,
                    predecessor_team,
                    base_ref,
                    base_commit,
                    env,
                    start_commit=str(previous.get("startCommit") or base_commit),
                    predecessor_team=str(previous.get("predecessorTeam") or "") or None,
                )
                start_commit = predecessor_identity["head"]
            else:
                start_commit = base_commit
            history = list(previous.get("history") or [])[-99:] if previous else []
            if previous:
                history.append({
                    "generation": int(previous.get("generation") or 1),
                    "runId": previous.get("runId"),
                    "workspaceId": workspace_id,
                    "team": previous.get("team"),
                    "state": previous.get("state"),
                    "deployedAt": previous.get("deployedAt"),
                    "headCommit": start_commit,
                    "evidenceWorkspace": str(repo / read_teamwork_root() / predecessor_team),
                })
                reopened_status = reopen_feature(
                    project, env, feature_id, dry_run=dry_run
                )
            else:
                reopened_status = None
            features[feature_id] = {
                "runId": run_key,
                "workspaceId": workspace_id,
                "featureId": feature_id,
                "featureTitle": title,
                "generation": generation,
                "history": history,
                "team": team,
                "preset": preset,
                "baseRef": base_ref,
                "baseCommit": base_commit,
                "startCommit": start_commit,
                **({"predecessorTeam": predecessor_team} if predecessor_team else {}),
                "repository": str(repo),
                "escalationTaskId": min(
                    (str(item.get("taskId")) for item in items if item.get("taskId")),
                    default="",
                ),
                "state": "discovered",
                "eligibility": "eligible",
                "eligibilityCheckedAt": iso_now(),
                "discoveredAt": iso_now(),
                **(
                    {
                        "reopenedAt": iso_now(),
                        "reopenedFeatureStatus": reopened_status,
                    }
                    if previous
                    else {}
                ),
            }
            new_feature_ids.add(feature_id)
            if previous:
                print(
                    f"pm-agent: reopened {safe_log_value(feature_id)} as generation {generation} "
                    f"and routed it to {preset} as {team}"
                )
            else:
                print(f"pm-agent: routed {safe_log_value(feature_id)} to {preset} as {team}")
            new_count += 1

        if reconcile_registered:
            candidates = [
                entry
                for entry in features.values()
                if entry.get("state") != "deployed" and entry.get("eligibility") == "eligible"
            ]
        else:
            candidates = [
                features[feature_id]
                for feature_id in new_feature_ids
                if features[feature_id].get("eligibility") == "eligible"
            ]
            skipped = sum(
                1
                for feature_id in registered_at_start
                if feature_id in features
                and features[feature_id].get("state") != "deployed"
                and features[feature_id].get("eligibility") == "eligible"
            )
            if skipped:
                print(f"pm-agent: reconcileRegisteredRuns=false; skipped {skipped} registered run(s)")
        candidates.sort(key=lambda entry: (str(entry.get("discoveredAt") or ""), str(entry.get("featureId"))))
        errors: list[str] = []
        for entry in candidates:
            try:
                reconcile_run(
                    entry,
                    project=project,
                    automation_root=automation_root,
                    env=env,
                    release_command=release_command,
                    deployment_config=deployment_config,
                    release_environment=release_environment,
                    dry_run=dry_run,
                )
            except MonitorError as exc:
                entry["lastError"] = str(exc)
                entry["lastErrorAt"] = iso_now()
                errors.append(f"{safe_log_value(entry.get('featureId'))}: {exc}")
                if not dry_run:
                    failure_kind = (
                        "production release handoff failed"
                        if entry.get("state") == "deployment-blocked"
                        else "deterministic supervisor reconciliation failed"
                    )
                    task_id = str(entry.get("escalationTaskId") or "")
                    if task_id:
                        delivery = "pm-run-failure-" + hashlib.sha256(
                            (str(entry.get("featureId")) + failure_kind).encode()
                        ).hexdigest()[:20]
                        try:
                            post_once(
                                task_id,
                                delivery,
                                failure_escalation_body(failure_kind),
                                root=project,
                                env=env,
                            )
                        except MonitorError as escalation_exc:
                            entry["trackerEscalationError"] = str(escalation_exc)
                # Continue reconciling independent registered [features].
            finally:
                if not dry_run:
                    registry["updatedAt"] = iso_now()
                    atomic_json(registry_path, registry)
        if not candidates:
            print("pm-agent: nothing actionable")
            if not dry_run:
                registry["updatedAt"] = iso_now()
                atomic_json(registry_path, registry)
        if errors:
            raise MonitorError("; ".join(errors))
        return 0
    finally:
        lease.release()


def print_cron() -> int:
    config, config_path, project, interpreter = bootstrap_automation()
    lifecycle_root = validate_team_safety(config, project)
    seconds = scan_interval_seconds(config)
    if seconds < 60 or seconds % 60:
        raise MonitorError("--print-cron requires the scan interval to be a whole number of minutes")
    minutes = seconds // 60
    if minutes < 60 and 60 % minutes == 0:
        schedule = "* * * * *" if minutes == 1 else f"*/{minutes} * * * *"
    elif seconds % 3600 == 0:
        hours = seconds // 3600
        if hours == 1:
            schedule = "0 * * * *"
        elif hours < 24 and 24 % hours == 0:
            schedule = f"0 */{hours} * * *"
        elif hours == 24:
            schedule = "0 0 * * *"
        else:
            raise MonitorError(
                "--print-cron cannot represent the scan interval as a stable cron cadence; "
                "use a minute divisor of 60 or a whole-hour divisor of 24"
            )
    else:
        raise MonitorError(
            "--print-cron cannot represent the scan interval as a stable cron cadence; "
            "use a minute divisor of 60 or a whole-hour divisor of 24"
        )
    automation_root = contained(project, str(config.get("workspaceRoot") or ""), "workspaceRoot")
    log = managed_path(automation_root, "cron.log", label="cron log")
    command = (
        "umask 077 && "
        f"mkdir -p {shlex.quote(str(log.parent))} && "
        f"cd {shlex.quote(str(project))} && "
        f"STARTUP_FACTORY_PROJECT_ROOT={shlex.quote(str(project))} "
        f"STARTUP_FACTORY_AUTOMATION_CONFIG={shlex.quote(str(config_path))} "
        f"STARTUP_FACTORY_LIFECYCLE_STATE_ROOT={shlex.quote(str(lifecycle_root))} "
        f"PATH={shlex.quote(ACTIVE_TRUSTED_PATH)} "
        f"{shlex.quote(str(interpreter))} -I -S -E -s {shlex.quote(str(Path(__file__).resolve()))} --once "
        f">> {shlex.quote(str(log))} 2>&1"
    )
    print(f"{schedule} {command}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--watch", action="store_true")
    mode.add_argument("--print-cron", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.print_cron:
        if args.dry_run:
            raise MonitorError("--dry-run does not combine with --print-cron")
        return print_cron()
    if args.once:
        return one_pass(dry_run=args.dry_run)
    if args.dry_run:
        raise MonitorError("--watch does not combine with --dry-run")
    config, _config_path, _project, _interpreter = bootstrap_automation()
    if type(config.get("enabled")) is not bool:
        raise MonitorError("enabled must be true or false")
    interval = scan_interval_seconds(config)
    print(f"pm-agent: watching every {interval}s; this process is the clock owner")
    while True:
        try:
            one_pass(dry_run=False)
        except MonitorError as exc:
            print(f"pm-agent: pass failed: {exc}", file=sys.stderr)
        time.sleep(interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MonitorError as exc:
        print(f"pm-agent: {exc}", file=sys.stderr)
        raise SystemExit(1)
