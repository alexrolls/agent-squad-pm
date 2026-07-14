#!/usr/bin/env python3
"""Deterministic feature-level product-acceptance envelopes.

Feature containers are not uniformly commentable.  The portable representation
is therefore stored on the lexicographically first task while remaining
explicitly scoped and bound to the whole feature.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone


MARKER_RE = re.compile(r"^\s*\[(product-approval|product-pushback)\](?:\s|$)")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


class ProductAcceptancePending(RuntimeError):
    """The gate has not produced one unambiguous, current approval."""


@dataclass(frozen=True)
class ProductAcceptance:
    digest: str
    anchor_task_id: str
    marker: str
    freshness: dict[str, str]
    body_digest: str


def canonical_digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def anchor_task_id(snapshot: dict) -> str:
    task_ids = sorted(str(task.get("taskId") or "") for task in snapshot.get("tasks") or [])
    if not task_ids or not task_ids[0]:
        raise ProductAcceptancePending("feature has no deterministic product-acceptance anchor task")
    if len(set(task_ids)) != len(task_ids):
        raise ProductAcceptancePending("feature has duplicate task identifiers; product-acceptance anchor is ambiguous")
    return task_ids[0]


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _parse_revision(value: object) -> int | None:
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"(?:markdown-offset:)?[0-9]+", value):
        return int(value.rsplit(":", 1)[-1])
    return None


def _fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    duplicates: set[str] = set()
    for line in body.splitlines()[1:]:
        match = re.fullmatch(r"([a-z][a-z0-9-]{0,63}):[ \t]*(.*?)\s*", line)
        if not match:
            continue
        key, value = match.groups()
        if key in fields:
            duplicates.add(key)
        else:
            fields[key] = value
    if duplicates:
        raise ProductAcceptancePending(
            "latest product verdict has duplicate canonical field(s): " + ", ".join(sorted(duplicates))
        )
    return fields


def _candidates(snapshot: dict) -> list[dict]:
    candidates: list[dict] = []
    for task in snapshot.get("tasks") or []:
        task_id = str(task.get("taskId") or "")
        for index, comment in enumerate(task.get("comments") or []):
            body = str(comment.get("body") or "")
            marker = MARKER_RE.match(body)
            if not marker:
                continue
            created_raw = comment.get("createdAt")
            updated_raw = comment.get("updatedAt")
            created = _parse_time(created_raw)
            updated = _parse_time(updated_raw)
            if updated_raw is not None:
                effective_time = updated
                effective_field = "updatedAt"
            else:
                effective_time = created
                effective_field = "createdAt"
            candidates.append(
                {
                    "taskId": task_id,
                    "index": index,
                    "marker": marker.group(1),
                    "body": body,
                    "commentId": None if comment.get("id") is None else str(comment.get("id")),
                    "author": None if comment.get("author") is None else str(comment.get("author")),
                    "createdAtRaw": created_raw,
                    "createdAt": created,
                    "updatedAtRaw": updated_raw,
                    "updatedAt": updated,
                    "effectiveTime": effective_time,
                    "effectiveField": effective_field,
                    "invalidTimestamp": bool(
                        (created_raw is not None and created is None)
                        or (updated_raw is not None and updated is None)
                    ),
                    "invalidChronology": bool(created and updated and updated < created),
                    "revisionRaw": comment.get("revision"),
                    "revision": _parse_revision(comment.get("revision")),
                }
            )
    return candidates


def _latest(candidates: list[dict]) -> tuple[dict, dict[str, str]]:
    if not candidates:
        raise ProductAcceptancePending("feature-level [product-approval] is missing")

    if any(candidate["invalidTimestamp"] for candidate in candidates):
        raise ProductAcceptancePending(
            "product verdict has an invalid createdAt/updatedAt timestamp"
        )
    if any(candidate["invalidChronology"] for candidate in candidates):
        raise ProductAcceptancePending(
            "product verdict has an updatedAt timestamp earlier than createdAt"
        )
    with_time = [candidate for candidate in candidates if candidate["effectiveTime"] is not None]
    with_revision = [candidate for candidate in candidates if candidate["revision"] is not None]
    if len(with_time) == len(candidates):
        ordered = sorted(candidates, key=lambda candidate: candidate["effectiveTime"])
        latest_key = ordered[-1]["effectiveTime"]
        if sum(candidate["effectiveTime"] == latest_key for candidate in candidates) != 1:
            timeline = {candidate["effectiveField"] for candidate in candidates}
            label = "createdAt values" if timeline == {"createdAt"} else "comment modification timestamps"
            raise ProductAcceptancePending(
                f"latest product verdict is ambiguous because tracker {label} tie"
            )
        latest = ordered[-1]
        return latest, {latest["effectiveField"]: latest["effectiveTime"].isoformat()}
    if len(with_revision) == len(candidates) and not with_time:
        ordered = sorted(candidates, key=lambda candidate: candidate["revision"])
        latest_key = ordered[-1]["revision"]
        if sum(candidate["revision"] == latest_key for candidate in candidates) != 1:
            raise ProductAcceptancePending(
                "latest product verdict is ambiguous because tracker revision values tie"
            )
        return ordered[-1], {"revision": str(ordered[-1]["revisionRaw"])}
    raise ProductAcceptancePending(
        "every product verdict needs one comparable createdAt/updatedAt timeline or one comparable numeric revision timeline"
    )


def evaluate(
    snapshot: dict,
    *,
    feature_id: str,
    commit: str,
    integration_evidence_digest: str,
) -> ProductAcceptance:
    """Return the exact current approval or raise a retryable pending verdict."""

    if str(snapshot.get("featureId") or "") != feature_id:
        raise ProductAcceptancePending("tracker snapshot is bound to a different feature")
    if not COMMIT_RE.fullmatch(commit) or not DIGEST_RE.fullmatch(integration_evidence_digest):
        raise ProductAcceptancePending("product-acceptance request has invalid integration bindings")
    anchor = anchor_task_id(snapshot)
    latest, freshness = _latest(_candidates(snapshot))
    if latest["marker"] == "product-pushback":
        raise ProductAcceptancePending("the latest feature-level product verdict is [product-pushback]")
    if latest["taskId"] != anchor:
        raise ProductAcceptancePending(
            f"feature-level [product-approval] must be stored on deterministic anchor task {anchor}"
        )
    fields = _fields(latest["body"])
    expected = {
        "scope": "feature",
        "feature-id": feature_id,
        "anchor-task-id": anchor,
        "commit": commit,
        "integration-evidence-digest": integration_evidence_digest,
        "acceptance-criteria": "passed",
    }
    mismatches = [key for key, value in expected.items() if fields.get(key) != value]
    if mismatches:
        raise ProductAcceptancePending(
            "latest [product-approval] is missing or stale for canonical field(s): "
            + ", ".join(mismatches)
        )
    body_digest = canonical_digest({"body": latest["body"]})
    material = {
        "schemaVersion": 1,
        "marker": "product-approval",
        "featureId": feature_id,
        "anchorTaskId": anchor,
        "commit": commit,
        "integrationEvidenceDigest": integration_evidence_digest,
        "acceptanceCriteria": "passed",
        "freshness": freshness,
        "commentId": latest["commentId"],
        "author": latest["author"],
        "bodyDigest": body_digest,
    }
    return ProductAcceptance(
        digest=canonical_digest(material),
        anchor_task_id=anchor,
        marker="product-approval",
        freshness=freshness,
        body_digest=body_digest,
    )


def request_payload(
    snapshot: dict,
    *,
    feature_id: str,
    commit: str,
    integration_evidence_digest: str,
    reason: str,
) -> dict:
    anchor = anchor_task_id(snapshot)
    body = "\n".join(
        [
            "[product-approval]",
            "scope: feature",
            f"feature-id: {feature_id}",
            f"anchor-task-id: {anchor}",
            f"commit: {commit}",
            f"integration-evidence-digest: {integration_evidence_digest}",
            "acceptance-criteria: passed",
            "summary: <feature-level acceptance evidence and conditions>",
            "",
            "— product-manager (team-lead only when no product role exists)",
        ]
    )
    return {
        "schemaVersion": 1,
        "state": "awaiting-product-approval",
        "featureId": feature_id,
        "anchorTaskId": anchor,
        "commit": commit,
        "integrationEvidenceDigest": integration_evidence_digest,
        "reason": reason,
        "canonicalBody": body,
    }


def validate_request(payload: dict, snapshot: dict) -> None:
    if payload.get("schemaVersion") != 1 or payload.get("state") != "awaiting-product-approval":
        raise ProductAcceptancePending("product-acceptance request has an unsupported schema/state")
    feature_id = str(payload.get("featureId") or "")
    commit = str(payload.get("commit") or "")
    evidence = str(payload.get("integrationEvidenceDigest") or "")
    if str(snapshot.get("featureId") or "") != feature_id:
        raise ProductAcceptancePending("product-acceptance request feature does not match the tracker snapshot")
    if payload.get("anchorTaskId") != anchor_task_id(snapshot):
        raise ProductAcceptancePending("product-acceptance request anchor does not match the tracker snapshot")
    if not COMMIT_RE.fullmatch(commit) or not DIGEST_RE.fullmatch(evidence):
        raise ProductAcceptancePending("product-acceptance request contains invalid integration bindings")
    expected = request_payload(
        snapshot,
        feature_id=feature_id,
        commit=commit,
        integration_evidence_digest=evidence,
        reason=str(payload.get("reason") or ""),
    )
    if payload.get("canonicalBody") != expected["canonicalBody"]:
        raise ProductAcceptancePending("product-acceptance request canonical body was modified")
