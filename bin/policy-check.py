#!/usr/bin/env python3
"""Fail-closed policy gate for privileged structured commands and release plans."""

from __future__ import annotations

import argparse
import json
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
    (r"(^|\s)(?:rm|rmdir)(?:\s|$)", "filesystem deletion is forbidden in the privileged executor"),
    (r"(^|\s)find(?:\s+\S+)*\s+-delete(?:\s|$)", "bulk filesystem deletion is forbidden"),
    (r"(^|\s)(?:dd|mkfs(?:\.\w+)?|fdisk|parted|shred)(?:\s|$)", "device/filesystem destruction is forbidden"),
    (r"\bgit\s+reset\s+--hard\b", "destructive git reset is forbidden"),
    (r"\bgit\s+clean\s+[^\n]*-[^\s]*f", "destructive git clean is forbidden"),
    (r"\bgit\s+push\s+[^\n]*(?:--force(?:-with-lease)?|-f)(?:\s|$)", "force push is forbidden"),
    (r"\bgit\s+(?:branch\s+-D|tag\s+-d)(?:\s|$)", "protected ref deletion is forbidden"),
    (r"\bDROP\s+(?:DATABASE|SCHEMA)\b", "database/schema drop is forbidden"),
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

ALLOWED_ACTIONS = {"deploy.plan", "deploy.status", "deploy.verify", "approval.verify"}
PREAUTH_ACTIONS = {"deploy.apply", "deploy.rollback"}
PLAN_ACTIONS = {"CREATE", "READ", "UPDATE", "REPLACE", "DELETE"}
SENSITIVE_RESOURCE_WORDS = {
    "database", "schema", "table", "data", "migration", "iam", "identity", "role",
    "policy", "network", "subnet", "firewall", "dns", "certificate", "secret", "kms",
    "key", "backup", "billing", "quota", "capacity", "traffic", "region",
}


def load_config(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot load guardrail config {path}: {exc}") from exc
    if value.get("schemaVersion") != 1:
        raise ValueError("guardrail config schemaVersion must be 1")
    return value


def result(decision: str, reason: str, **extra: object) -> dict:
    return {"decision": decision, "reason": reason, **extra}


def emit(value: dict) -> int:
    print(json.dumps(value, sort_keys=True))
    return {ALLOW: 0, APPROVAL: 2, DENY: 3}[value["decision"]]


def evaluate_command(action: str, environment: str, argv: list[str], preauthorized: bool, config: dict) -> dict:
    if not argv or not all(isinstance(token, str) and token and "\x00" not in token for token in argv):
        return result(DENY, "command must be a non-empty structured argv array")
    normalized = " ".join(argv)
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
    if action in ALLOWED_ACTIONS:
        return result(ALLOW, f"{action} is read-only or independently verifying")
    if action in PREAUTH_ACTIONS:
        if environment != "production":
            return result(DENY, "privileged release hooks are production-only in this executor")
        if preauthorized:
            return result(ALLOW, f"{action} is bound to a verified/pre-authorized release manifest")
        return result(APPROVAL, f"{action} requires an exact release authorization")
    if action in set(config.get("additionalApprovalRequiredActions") or []):
        return result(APPROVAL, f"project policy requires approval for {action}")
    return result(DENY, f"unknown privileged action: {action}")


def evaluate_plan(plan: dict, mode: str, approved: bool, config: dict) -> dict:
    if plan.get("schemaVersion") != 1:
        return result(DENY, "release plan schemaVersion must be 1")
    if plan.get("environment") != "production":
        return result(DENY, "release plan environment must be production")
    if not re.fullmatch(r"[0-9a-f]{40,64}", str(plan.get("commit") or "")):
        return result(DENY, "release plan must bind a full immutable commit hash")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(plan.get("artifactDigest") or "")):
        return result(DENY, "release plan must bind a sha256 artifact digest")
    changes = plan.get("changes")
    if not isinstance(changes, list):
        return result(DENY, "release plan changes must be a list")
    maximum = int(config.get("maximumAutomaticPlanChanges", 100))
    if len(changes) > maximum:
        return result(APPROVAL, f"plan has {len(changes)} changes; maximum automatic count is {maximum}")
    approval_reasons: list[str] = []
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            return result(DENY, f"plan change {index} is not an object")
        operation = str(change.get("action") or "").upper()
        if operation not in PLAN_ACTIONS:
            return result(DENY, f"unknown plan action at change {index}: {operation or '<empty>'}")
        if operation in {"DELETE", "REPLACE"}:
            return result(DENY, f"autonomous {operation} is forbidden at change {index}")
        resource = " ".join(str(change.get(key) or "") for key in ("resourceType", "resourceId")).lower()
        if operation in {"CREATE", "UPDATE"} and any(word in resource for word in SENSITIVE_RESOURCE_WORDS):
            approval_reasons.append(f"sensitive {operation} at change {index} ({resource.strip()})")
        if bool(change.get("publicExposure")):
            approval_reasons.append(f"public exposure at change {index}")
        try:
            cost_delta = float(change.get("estimatedCostDelta") or 0)
        except (TypeError, ValueError):
            return result(DENY, f"invalid estimatedCostDelta at change {index}")
        if cost_delta > float(config.get("maximumAutomaticCostDelta", 0)):
            approval_reasons.append(f"cost delta {cost_delta} at change {index}")
    if approval_reasons and not approved:
        return result(APPROVAL, "; ".join(approval_reasons))
    if approval_reasons and mode == "automatic":
        return result(DENY, "automatic mode cannot authorize policy-sensitive plan changes")
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
    command.add_argument("--preauthorized", action="store_true")
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
            value = evaluate_command(args.action, args.environment, argv, args.preauthorized, config)
        else:
            try:
                payload = json.loads(args.plan_file.read_text())
            except (OSError, ValueError) as exc:
                value = result(DENY, f"cannot load release plan: {exc}")
            else:
                value = evaluate_plan(payload, args.mode, args.approved, config)
    except ValueError as exc:
        value = result(DENY, str(exc))
    return emit(value)


if __name__ == "__main__":
    raise SystemExit(main())
