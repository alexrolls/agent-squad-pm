#!/usr/bin/env python3
"""Fail-closed policy gate for privileged structured commands and release plans."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path


ALLOW = "ALLOW"
APPROVAL = "REQUIRE_HUMAN_APPROVAL"
DENY = "DENY"

# This baseline is code-owned. Project configuration may add denials, never remove one.
DENY_COMMAND_PATTERNS = (
    (r"[\n\r;&|><`$]", "shell composition/redirection is forbidden"),
    (r"(^|\s)(?:sudo|doas)(?:\s|$)", "privilege escalation is forbidden"),
    (r"(^|\s)(?:ba|z|c|k|fi)?sh\s+-c(?:\s|$)", "opaque shell execution is forbidden"),
    (r"(^|\s)(?:eval|exec|xargs)(?:\s|$)", "opaque command execution is forbidden"),
    (r"(?:^|[/\s])(?:rm|rmdir)(?:\s|$)", "filesystem deletion is forbidden in the privileged executor"),
    (r"(^|\s)find(?:\s+\S+)*\s+-delete(?:\s|$)", "bulk filesystem deletion is forbidden"),
    (r"(^|\s)(?:dd|mkfs(?:\.\w+)?|fdisk|parted|shred)(?:\s|$)", "device/filesystem destruction is forbidden"),
    (r"\bgit\s+reset\s+--hard\b", "destructive git reset is forbidden"),
    (r"\bgit\s+clean\s+[^\n]*-[^\s]*f", "destructive git clean is forbidden"),
    (r"\bgit\s+push\s+[^\n]*(?:--force(?:-with-lease)?|-f)(?:\s|$)", "force push is forbidden"),
    (r"\bgit\s+(?:branch\s+-D|tag\s+-d)(?:\s|$)", "protected ref deletion is forbidden"),
    (r"\bDROP\s+(?:DATABASE|SCHEMA|TABLE|INDEX|VIEW|TYPE|ROLE|USER|EXTENSION)\b", "database object drop is forbidden"),
    (r"\bTRUNCATE(?:\s+TABLE)?\b", "database truncation is forbidden"),
    (r"\bDELETE\s+FROM\b(?![^;]*\bWHERE\b)", "unbounded database deletion is forbidden"),
    (r"\bterraform\s+destroy\b", "infrastructure destroy is forbidden"),
    (r"\bterraform\s+apply\b[^\n]*\s-destroy\b", "infrastructure destroy is forbidden"),
    (r"\bpulumi\s+destroy\b", "infrastructure destroy is forbidden"),
    (r"\bkubectl\s+delete\b", "cluster resource deletion is forbidden"),
    (r"\bhelm\s+uninstall\b", "release deletion is forbidden"),
    (r"\b(?:delete|destroy|terminate|purge|deprovision)[-_ ](?:instance|cluster|database|db|volume|disk|network|subnet|vpc|key|secret|backup|log|bucket|dns|certificate)s?\b", "resource deletion is forbidden"),
    (r"(^|\s)(?:env|printenv)(?:\s|$)", "environment/secret dumping is forbidden"),
    (r"169\.254\.169\.254|metadata\.google\.internal", "metadata credential access is forbidden"),
    (r"\b(?:disable|delete)[-_ ](?:audit|logging|backup|monitoring|alarm|waf|mfa|policy)\b", "control-plane safeguard removal is forbidden"),
    (r"\b(?:base64|openssl)\b[^\n]*(?:-d|--decode)\b", "encoded command bypass is forbidden"),
)

ALLOWED_ACTIONS = {"deploy.plan", "deploy.status", "deploy.verify", "approval.verify", "delivery.verify"}
PREAUTH_ACTIONS = {"deploy.apply", "deploy.rollback"}
PLAN_ACTIONS = {"CREATE", "READ", "UPDATE", "REPLACE", "DELETE"}
RESOURCE_CLASSES = {
    "application", "compute", "configuration", "database", "storage", "network",
    "identity", "security-control", "secret", "dns", "certificate", "backup",
    "observability", "billing", "quota", "traffic", "other",
}
# A compute mutation is a production capacity/infrastructure change, not merely
# an application rollout. Keep only artifact/config/telemetry changes eligible
# for automatic mode; every other class needs an exact external approval.
SENSITIVE_RESOURCE_CLASSES = RESOURCE_CLASSES - {"application", "configuration", "observability"}
DATA_EFFECTS = {"none", "read-only", "additive", "mutating", "destructive"}
PLAN_FIELDS = {
    "schemaVersion", "environment", "target", "commit", "sourceArchiveDigest",
    "artifactDigest", "changes", "rollback",
}
CHANGE_FIELDS = {
    "action", "resourceClass", "resourceId", "destructive", "reversible",
    "publicExposure", "dataEffect", "estimatedCostDelta", "secretValueAccess",
    "privilegeEscalation", "disablesSafeguard",
}
ROLLBACK_FIELDS = {"automaticSafe", "previousArtifactDigest"}
DESTRUCTIVE_EXECUTABLES = {
    "rm", "rmdir", "dd", "shred", "fdisk", "parted", "wipefs", "mkfs", "mkfs.ext4",
    "mkfs.xfs", "dropdb",
}
FORBIDDEN_EXECUTABLES = {"sudo", "doas", "env", "printenv", "xargs", "eval"}
OPAQUE_INTERPRETERS = {
    "sh": "-c", "bash": "-c", "zsh": "-c", "csh": "-c", "ksh": "-c", "fish": "-c",
    "python": "-c", "python3": "-c", "perl": "-e", "ruby": "-e", "node": "-e",
}
DESTRUCTIVE_TOKENS = {
    "delete", "destroy", "terminate", "purge", "deprovision", "uninstall", "drop", "truncate",
}


def reject_duplicate_object_keys(pairs: list[tuple[str, object]]) -> dict:
    """Build a JSON object while rejecting parser-differential ambiguity."""
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def parse_json(text: str) -> object:
    return json.loads(text, object_pairs_hook=reject_duplicate_object_keys)


def load_config(path: Path) -> dict:
    try:
        value = parse_json(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot load guardrail config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("guardrail config must be a JSON object")
    if value.get("schemaVersion") != 1:
        raise ValueError("guardrail config schemaVersion must be 1")
    return value


def result(decision: str, reason: str, **extra: object) -> dict:
    return {"decision": decision, "reason": reason, **extra}


def emit(value: dict) -> int:
    print(json.dumps(value, sort_keys=True))
    return {ALLOW: 0, APPROVAL: 2, DENY: 3}[value["decision"]]


def finite_json_number(value: object) -> float | None:
    """Return a finite float for a JSON number; reject booleans and coercions."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def evaluate_command(action: str, environment: str, argv: list[str], authorization_digest: str | None, config: dict) -> dict:
    if not argv or not all(isinstance(token, str) and token and "\x00" not in token for token in argv):
        return result(DENY, "command must be a non-empty structured argv array")
    normalized = " ".join(argv)
    executable = Path(argv[0]).name.lower()
    if executable in DESTRUCTIVE_EXECUTABLES or executable.startswith("mkfs."):
        return result(DENY, f"destructive executable is forbidden: {executable}")
    if executable in FORBIDDEN_EXECUTABLES:
        return result(DENY, f"privileged/opaque executable is forbidden: {executable}")
    if executable in OPAQUE_INTERPRETERS and OPAQUE_INTERPRETERS[executable] in argv[1:]:
        return result(DENY, f"inline opaque code is forbidden through {executable}")
    for token in argv[1:]:
        normalized_token = token.strip().lower().replace("_", "-")
        words = {word for word in re.split(r"[^a-z0-9-]+", normalized_token) if word}
        if words.intersection(DESTRUCTIVE_TOKENS):
            return result(DENY, f"destructive command token is forbidden: {sorted(words.intersection(DESTRUCTIVE_TOKENS))[0]}")
        if re.search(r"(?:^|-)(?:delete|destroy|terminate|purge|deprovision|uninstall)(?:-|$)", normalized_token):
            return result(DENY, "resource deletion/destruction command is forbidden")
    for pattern, reason in DENY_COMMAND_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return result(DENY, reason, pattern=pattern)
    for pattern in config.get("additionalDenyPatterns") or []:
        try:
            matched = re.search(pattern, normalized, re.IGNORECASE)
        except re.error as exc:
            return result(DENY, f"invalid additional deny pattern: {exc}")
        if matched:
            return result(DENY, "project guardrail denied the command", pattern=pattern)
    authorization_valid = bool(
        authorization_digest and re.fullmatch(r"sha256:[0-9a-f]{64}", authorization_digest)
    )
    project_requires_approval = action in set(config.get("additionalApprovalRequiredActions") or [])
    if project_requires_approval and not authorization_valid:
        return result(APPROVAL, f"project policy requires exact authorization for {action}")
    if action in PREAUTH_ACTIONS:
        if environment != "production":
            return result(DENY, "privileged release hooks are production-only in this executor")
        if authorization_valid:
            return result(ALLOW, f"{action} is bound to an immutable authorization digest")
        return result(APPROVAL, f"{action} requires an exact release authorization")
    if action in ALLOWED_ACTIONS:
        if project_requires_approval:
            return result(ALLOW, f"{action} satisfies the project-required exact authorization")
        return result(ALLOW, f"{action} passed the privileged-command deny policy")
    return result(DENY, f"unknown privileged action: {action}")


def evaluate_plan(plan: dict, mode: str, approved: bool, config: dict) -> dict:
    if not isinstance(plan, dict) or set(plan) != PLAN_FIELDS:
        missing = sorted(PLAN_FIELDS - set(plan)) if isinstance(plan, dict) else sorted(PLAN_FIELDS)
        unknown = sorted(set(plan) - PLAN_FIELDS) if isinstance(plan, dict) else []
        return result(DENY, f"release plan must use the exact closed schema (missing={missing}, unknown={unknown})")
    if type(plan.get("schemaVersion")) is not int or plan.get("schemaVersion") != 1:
        return result(DENY, "release plan schemaVersion must be 1")
    if plan.get("environment") != "production":
        return result(DENY, "release plan environment must be production")
    target = plan.get("target")
    if (
        not isinstance(target, dict)
        or not target
        or any(not re.fullmatch(r"[a-z][a-zA-Z0-9_-]{0,63}", str(key)) for key in target)
        or any(
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 512
            or any(ord(char) < 32 for char in value)
            for value in target.values()
        )
    ):
        return result(DENY, "release plan target must be a bounded non-empty string map")
    if not re.fullmatch(r"[0-9a-f]{40}", str(plan.get("commit") or "")):
        return result(DENY, "release plan must bind a full immutable commit hash")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(plan.get("sourceArchiveDigest") or "")):
        return result(DENY, "release plan must bind the protected source archive digest")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(plan.get("artifactDigest") or "")):
        return result(DENY, "release plan must bind a sha256 artifact digest")
    changes = plan.get("changes")
    if not isinstance(changes, list):
        return result(DENY, "release plan changes must be a list")
    if not changes:
        return result(DENY, "release plan must contain at least one normalized change")
    approval_reasons: list[str] = []
    maximum = config.get("maximumAutomaticPlanChanges", 100)
    if type(maximum) is not int or maximum < 1:
        return result(DENY, "maximumAutomaticPlanChanges must be a positive integer")
    if len(changes) > maximum:
        approval_reasons.append(f"plan has {len(changes)} changes; maximum automatic count is {maximum}")
    maximum_cost_raw = config.get("maximumAutomaticCostDelta", 0)
    maximum_cost = finite_json_number(maximum_cost_raw)
    if maximum_cost is None:
        return result(DENY, "maximumAutomaticCostDelta must be a finite JSON number")
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            return result(DENY, f"plan change {index} is not an object")
        if set(change) != CHANGE_FIELDS:
            return result(
                DENY,
                f"plan change {index} must use the exact closed schema "
                f"(missing={sorted(CHANGE_FIELDS - set(change))}, unknown={sorted(set(change) - CHANGE_FIELDS)})",
            )
        operation = str(change.get("action") or "").upper()
        if operation not in PLAN_ACTIONS:
            return result(DENY, f"unknown plan action at change {index}: {operation or '<empty>'}")
        if operation in {"DELETE", "REPLACE"}:
            return result(DENY, f"autonomous {operation} is forbidden at change {index}")
        resource_class = change.get("resourceClass")
        if resource_class not in RESOURCE_CLASSES:
            return result(DENY, f"unknown resourceClass at change {index}: {resource_class or '<empty>'}")
        resource_id = change.get("resourceId")
        if not isinstance(resource_id, str) or not resource_id.strip() or len(resource_id) > 1024 or any(ord(char) < 32 for char in resource_id):
            return result(DENY, f"plan change {index} requires a bounded resourceId")
        for required_bool in (
            "destructive", "reversible", "publicExposure", "secretValueAccess",
            "privilegeEscalation", "disablesSafeguard",
        ):
            if not isinstance(change.get(required_bool), bool):
                return result(DENY, f"plan change {index} requires boolean {required_bool}")
        data_effect = change.get("dataEffect")
        if data_effect not in DATA_EFFECTS:
            return result(DENY, f"unknown dataEffect at change {index}: {data_effect or '<empty>'}")
        if change["destructive"] or data_effect == "destructive":
            return result(DENY, f"destructive effect is forbidden at change {index}")
        if resource_class == "secret" and operation == "READ":
            return result(DENY, f"secret-resource reads are forbidden at change {index}")
        if change["secretValueAccess"]:
            return result(DENY, f"secret-value access is forbidden at change {index}")
        if change["privilegeEscalation"]:
            return result(DENY, f"wildcard/administrator privilege escalation is forbidden at change {index}")
        if change["disablesSafeguard"]:
            return result(DENY, f"disabling an audit/security safeguard is forbidden at change {index}")
        if operation in {"CREATE", "UPDATE"} and resource_class in SENSITIVE_RESOURCE_CLASSES:
            approval_reasons.append(f"sensitive {operation} at change {index} ({resource_class})")
        if operation != "READ" and not change["reversible"]:
            approval_reasons.append(f"non-reversible {operation} at change {index}")
        if data_effect in {"additive", "mutating"}:
            approval_reasons.append(f"data effect {data_effect} at change {index}")
        if change["publicExposure"]:
            approval_reasons.append(f"public exposure at change {index}")
        cost_delta_raw = change.get("estimatedCostDelta", 0)
        cost_delta = finite_json_number(cost_delta_raw)
        if cost_delta is None:
            return result(DENY, f"estimatedCostDelta at change {index} must be a finite JSON number")
        if cost_delta > maximum_cost:
            approval_reasons.append(f"cost delta {cost_delta} at change {index}")
    rollback = plan.get("rollback")
    if rollback is not None:
        if not isinstance(rollback, dict) or set(rollback) != ROLLBACK_FIELDS:
            return result(DENY, "rollback must use the exact closed schema")
        if not isinstance(rollback.get("automaticSafe"), bool):
            return result(DENY, "rollback must declare a boolean automaticSafe")
        previous = rollback.get("previousArtifactDigest")
        if previous is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", str(previous)):
            return result(DENY, "rollback previousArtifactDigest must be null or sha256")
        if rollback["automaticSafe"]:
            if not config.get("allowAutomaticRollbackOnlyToPreviousArtifact", True):
                return result(DENY, "project guardrails disable automatic rollback")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(previous or "")):
                return result(DENY, "automatic rollback must bind the previous artifact digest")
    if approval_reasons and mode == "automatic":
        return result(DENY, "automatic mode cannot authorize policy-sensitive plan changes")
    if approval_reasons and not approved:
        return result(APPROVAL, "; ".join(approval_reasons))
    if mode == "approval-required" and not approved:
        return result(APPROVAL, "production apply requires an external exact-manifest authorization")
    if mode not in {"automatic", "approval-required"}:
        return result(DENY, f"unknown deployment mode: {mode}")
    return result(ALLOW, "plan is non-destructive and bound to the reviewed artifact")


def main() -> int:
    default_config = Path(__file__).resolve().parent.parent / "config" / "guardrails.config.json"
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=default_config)
    sub = parser.add_subparsers(dest="operation", required=True)

    command = sub.add_parser("command")
    command.add_argument("--action", required=True)
    command.add_argument("--environment", required=True)
    command.add_argument("--authorization-digest")
    command.add_argument("argv", nargs=argparse.REMAINDER)

    plan = sub.add_parser("plan")
    plan.add_argument("--mode", required=True)
    plan.add_argument("--approved", action="store_true")
    plan.add_argument("plan_file", type=Path)

    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.operation == "command":
            argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            value = evaluate_command(args.action, args.environment, argv, args.authorization_digest, config)
        else:
            try:
                payload = parse_json(args.plan_file.read_text())
            except (OSError, ValueError) as exc:
                value = result(DENY, f"cannot load release plan: {exc}")
            else:
                value = evaluate_plan(payload, args.mode, args.approved, config)
    except ValueError as exc:
        value = result(DENY, str(exc))
    return emit(value)


if __name__ == "__main__":
    raise SystemExit(main())
