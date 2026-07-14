#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from product_acceptance import ProductAcceptancePending, evaluate, request_payload  # noqa: E402


FEATURE = "FEATURE-1"
COMMIT = "a" * 40
EVIDENCE = "sha256:" + "b" * 64


def snapshot() -> dict:
    return {
        "featureId": FEATURE,
        "tasks": [
            {"taskId": "TASK-2", "comments": []},
            {"taskId": "TASK-1", "comments": []},
        ],
    }


def approval_body(data: dict) -> str:
    return request_payload(
        data,
        feature_id=FEATURE,
        commit=COMMIT,
        integration_evidence_digest=EVIDENCE,
        reason="missing",
    )["canonicalBody"]


def add(data: dict, task_id: str, body: str, *, revision=None, created_at=None, updated_at=None) -> None:
    task = next(task for task in data["tasks"] if task["taskId"] == task_id)
    task["comments"].append(
        {
            "id": f"c-{task_id}-{len(task['comments'])}",
            "body": body,
            "revision": revision,
            "createdAt": created_at,
            "updatedAt": updated_at,
        }
    )


class ProductAcceptanceTest(unittest.TestCase):
    def test_exact_anchor_approval_is_bound(self):
        data = snapshot()
        add(data, "TASK-1", approval_body(data), revision="markdown-offset:100")
        result = evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)
        self.assertEqual(result.anchor_task_id, "TASK-1")
        self.assertRegex(result.digest, r"^sha256:[0-9a-f]{64}$")

    def test_later_pushback_blocks_earlier_approval(self):
        data = snapshot()
        add(data, "TASK-1", approval_body(data), revision=100)
        add(data, "TASK-2", "[product-pushback]\nreason: criterion failed", revision=101)
        with self.assertRaisesRegex(ProductAcceptancePending, "latest.*product-pushback"):
            evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)

    def test_fresh_approval_after_pushback_unblocks(self):
        data = snapshot()
        add(data, "TASK-1", approval_body(data), revision=100)
        add(data, "TASK-2", "[product-pushback]\nreason: criterion failed", revision=101)
        add(data, "TASK-1", approval_body(data) + "\ncondition: retested", revision=102)
        evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)

    def test_non_anchor_approval_is_not_feature_approval(self):
        data = snapshot()
        add(data, "TASK-2", approval_body(data), revision=100)
        with self.assertRaisesRegex(ProductAcceptancePending, "anchor task TASK-1"):
            evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)

    def test_stale_commit_is_retryable_pending(self):
        data = snapshot()
        add(data, "TASK-1", approval_body(data).replace(COMMIT, "c" * 40), revision=100)
        with self.assertRaisesRegex(ProductAcceptancePending, "commit"):
            evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)

    def test_missing_freshness_fails_closed(self):
        data = snapshot()
        add(data, "TASK-1", approval_body(data))
        with self.assertRaisesRegex(ProductAcceptancePending, "comparable"):
            evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)

    def test_tied_created_at_fails_closed(self):
        data = snapshot()
        timestamp = "2026-07-14T12:00:00+00:00"
        add(data, "TASK-1", approval_body(data), created_at=timestamp)
        add(data, "TASK-2", "[product-pushback]\nreason: tied", created_at=timestamp)
        with self.assertRaisesRegex(ProductAcceptancePending, "createdAt values tie"):
            evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)

    def test_mixed_freshness_timelines_fail_closed(self):
        data = snapshot()
        add(data, "TASK-1", approval_body(data), created_at="2026-07-14T12:00:00Z")
        add(data, "TASK-2", "[product-pushback]\nreason: mixed", revision=200)
        with self.assertRaisesRegex(ProductAcceptancePending, "comparable"):
            evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)

    def test_editing_old_pushback_makes_it_the_current_verdict(self):
        data = snapshot()
        add(
            data,
            "TASK-2",
            "[product-pushback]\nreason: retest failed",
            created_at="2026-07-14T10:00:00Z",
            updated_at="2026-07-14T13:00:00Z",
        )
        add(
            data,
            "TASK-1",
            approval_body(data),
            created_at="2026-07-14T11:00:00Z",
            updated_at="2026-07-14T11:00:00Z",
        )
        with self.assertRaisesRegex(ProductAcceptancePending, "latest.*product-pushback"):
            evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)

    def test_edited_approval_uses_updated_at_and_revalidates_body(self):
        data = snapshot()
        add(
            data,
            "TASK-2",
            "[product-pushback]\nreason: old",
            created_at="2026-07-14T10:00:00Z",
            updated_at="2026-07-14T10:00:00Z",
        )
        add(
            data,
            "TASK-1",
            approval_body(data),
            created_at="2026-07-14T09:00:00Z",
            updated_at="2026-07-14T12:00:00Z",
        )
        result = evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)
        self.assertEqual({"updatedAt": "2026-07-14T12:00:00+00:00"}, result.freshness)

        data["tasks"][1]["comments"][0]["body"] = approval_body(data).replace(COMMIT, "c" * 40)
        data["tasks"][1]["comments"][0]["updatedAt"] = "2026-07-14T13:00:00Z"
        with self.assertRaisesRegex(ProductAcceptancePending, "commit"):
            evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)

    def test_update_before_creation_fails_closed(self):
        data = snapshot()
        add(
            data,
            "TASK-1",
            approval_body(data),
            created_at="2026-07-14T12:00:00Z",
            updated_at="2026-07-14T11:00:00Z",
        )
        with self.assertRaisesRegex(ProductAcceptancePending, "earlier than createdAt"):
            evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)

    def test_malformed_update_timestamp_cannot_fall_back_to_revision(self):
        data = snapshot()
        add(
            data,
            "TASK-1",
            approval_body(data),
            revision=999,
            created_at="2026-07-14T12:00:00Z",
            updated_at="not-a-time",
        )
        with self.assertRaisesRegex(ProductAcceptancePending, "invalid createdAt/updatedAt"):
            evaluate(data, feature_id=FEATURE, commit=COMMIT, integration_evidence_digest=EVIDENCE)


if __name__ == "__main__":
    unittest.main()
