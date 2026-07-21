#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from review_evidence import (  # noqa: E402
    EvidenceError,
    bind_approval,
    bind_request,
    validate,
)


BASE = "a" * 40
HEAD = "b" * 40
PACKAGE = "sha256:" + "c" * 64


def approved_snapshot(review_gates: tuple[str, ...] = ()) -> dict:
    request = bind_request(
        "[review-request]\nFiles: app.py\n\n- backend\n",
        BASE,
        HEAD,
        PACKAGE,
        review_gates,
    )
    team_lead = bind_approval(
        "[team-lead-approval]\nFiles: app.py\n\n- team-lead\n",
        request,
        "team-lead",
        "gate:team-lead:1",
    )
    architecture = bind_approval(
        "[architecture-approval]\nFiles: app.py\n\n- principal-architect\n",
        request,
        "principal-architect",
        "gate:principal-architect:1",
    )
    sceptical = bind_approval(
        "[sceptical-architecture-approval]\nFiles: app.py\n\n- sceptical-architect\n",
        request,
        "sceptical-architect",
        "gate:sceptical-architect:1",
    )
    comments = [{"id": "request", "body": request, "author": "backend", "createdAt": "1"}]
    if "security" in review_gates:
        security = bind_approval(
            "[security-approval]\nFiles: app.py\n\n- senior-security-engineer\n",
            request,
            "senior-security-engineer",
            "gate:senior-security-engineer:1",
        )
        comments.append(
            {"id": "security", "body": security, "author": "senior-security-engineer", "createdAt": "2"}
        )
    comments.extend([
        {"id": "team-lead", "body": team_lead, "author": "team-lead", "createdAt": "3"},
        {"id": "architecture", "body": architecture, "author": "principal-architect", "createdAt": "4"},
        {"id": "sceptical-architecture", "body": sceptical, "author": "sceptical-architect", "createdAt": "5"},
    ])
    return {
        "featureId": "FEATURE-1",
        "tasks": [{
            "taskId": "TASK-1",
            "status": "Review",
            "description": "review-gates: " + ",".join(review_gates) if review_gates else "",
            "comments": comments,
        }],
    }


def require_qa(data: dict) -> str:
    task = data["tasks"][0]
    request = task["comments"][0]["body"]
    approval = bind_approval(
        "[review-approval]\nFiles: app.py\n\n- senior-qa-engineer\n",
        request,
        "senior-qa-engineer",
        "gate:senior-qa-engineer:1",
    )
    task["comments"].insert(
        1,
        {"id": "qa", "body": approval, "author": "senior-qa-engineer", "createdAt": "6"}
    )
    return approval


class ReviewEvidenceTest(unittest.TestCase):
    def test_independent_three_party_approval_is_bound_to_exact_package(self):
        result = validate(
            approved_snapshot(),
            "TASK-1",
            base=BASE,
            head=HEAD,
            package=PACKAGE,
            review_statuses={"Review"},
        )
        self.assertRegex(result, r"^sha256:[0-9a-f]{64}$")

    def test_missing_security_approval_keeps_release_gate_closed(self):
        data = approved_snapshot(("security",))
        data["tasks"][0]["comments"].pop(1)
        with self.assertRaisesRegex(EvidenceError, r"required \[security-approval\]"):
            validate(data, "TASK-1", base=BASE, head=HEAD, package=PACKAGE)

    def test_same_file_branch_movement_invalidates_approvals(self):
        with self.assertRaisesRegex(EvidenceError, "exact current base/head/package"):
            validate(
                approved_snapshot(),
                "TASK-1",
                base=BASE,
                head="d" * 40,
                package=PACKAGE,
            )

    def test_approval_cannot_be_reused_for_another_request(self):
        data = approved_snapshot()
        data["tasks"][0]["comments"][1]["body"] = data["tasks"][0]["comments"][1][
            "body"
        ].replace("Review-Request-SHA256: sha256:", "Review-Request-SHA256: sha256:" + "0")
        with self.assertRaisesRegex(EvidenceError, "exactly one|not bound"):
            validate(data, "TASK-1", base=BASE, head=HEAD, package=PACKAGE)

    def test_later_finding_invalidates_both_approvals(self):
        data = approved_snapshot()
        data["tasks"][0]["comments"].append(
            {"id": "finding", "body": "[review-findings]\nMust fix", "createdAt": "6"}
        )
        with self.assertRaisesRegex(EvidenceError, "independently three-party-approved"):
            validate(data, "TASK-1", base=BASE, head=HEAD, package=PACKAGE)

    def test_new_request_needs_new_approvals(self):
        data = approved_snapshot()
        data["tasks"][0]["comments"].append(
            {
                "id": "request-2",
                "body": bind_request("[review-request]\nFiles: app.py\n", BASE, HEAD, PACKAGE),
                "createdAt": "6",
            }
        )
        with self.assertRaisesRegex(EvidenceError, "independently three-party-approved"):
            validate(data, "TASK-1", base=BASE, head=HEAD, package=PACKAGE)

    def test_duplicate_reviewer_context_is_not_independent(self):
        data = approved_snapshot()
        data["tasks"][0]["comments"][2]["body"] = data["tasks"][0]["comments"][2][
            "body"
        ].replace("gate:principal-architect:1", "gate:team-lead:1")
        with self.assertRaisesRegex(EvidenceError, "three distinct reviewer contexts"):
            validate(data, "TASK-1", base=BASE, head=HEAD, package=PACKAGE)

    def test_duplicate_reviewer_role_is_not_independent(self):
        data = approved_snapshot()
        data["tasks"][0]["comments"][2]["body"] = data["tasks"][0]["comments"][2][
            "body"
        ].replace("Reviewer-Role: principal-architect", "Reviewer-Role: team-lead")
        with self.assertRaisesRegex(EvidenceError, "three distinct reviewer roles"):
            validate(data, "TASK-1", base=BASE, head=HEAD, package=PACKAGE)

    def test_required_qa_approval_is_bound_and_included(self):
        data = approved_snapshot(("qa",))
        require_qa(data)
        result = validate(data, "TASK-1", base=BASE, head=HEAD, package=PACKAGE)
        self.assertRegex(result, r"^sha256:[0-9a-f]{64}$")

    def test_missing_required_qa_approval_keeps_gate_closed(self):
        data = approved_snapshot(("qa",))
        with self.assertRaisesRegex(EvidenceError, r"required \[review-approval\]"):
            validate(data, "TASK-1", base=BASE, head=HEAD, package=PACKAGE)

    def test_stale_required_qa_approval_keeps_gate_closed(self):
        data = approved_snapshot(("qa",))
        qa = require_qa(data)
        task = data["tasks"][0]
        task["comments"].insert(0, task["comments"].pop(1))
        with self.assertRaisesRegex(EvidenceError, r"required \[review-approval\]"):
            validate(data, "TASK-1", base=BASE, head=HEAD, package=PACKAGE)

    def test_required_qa_must_use_an_independent_context(self):
        data = approved_snapshot(("qa",))
        require_qa(data)
        task = data["tasks"][0]
        task["comments"][1]["body"] = task["comments"][1]["body"].replace(
            "gate:senior-qa-engineer:1", "gate:team-lead:1"
        )
        with self.assertRaisesRegex(EvidenceError, "reuses a reviewer context"):
            validate(data, "TASK-1", base=BASE, head=HEAD, package=PACKAGE)

    def test_team_lead_approval_must_follow_required_qa(self):
        data = approved_snapshot(("qa",))
        require_qa(data)
        task = data["tasks"][0]
        task["comments"].append(task["comments"].pop(1))
        with self.assertRaisesRegex(EvidenceError, "team-lead approval must be newer"):
            validate(data, "TASK-1", base=BASE, head=HEAD, package=PACKAGE)

    def test_review_gate_metadata_drift_invalidates_the_request(self):
        data = approved_snapshot(("qa",))
        require_qa(data)
        data["tasks"][0]["description"] = ""
        with self.assertRaisesRegex(EvidenceError, "Review-Gates do not match"):
            validate(data, "TASK-1", base=BASE, head=HEAD, package=PACKAGE)

    def test_preset_required_security_gate_is_enforced_without_task_metadata(self):
        data = approved_snapshot(("security",))
        data["tasks"][0]["description"] = ""
        result = validate(
            data,
            "TASK-1",
            base=BASE,
            head=HEAD,
            package=PACKAGE,
            required_gates=("security",),
        )
        self.assertRegex(result, r"^sha256:[0-9a-f]{64}$")

    def test_preset_required_security_gate_must_be_bound_to_request(self):
        with self.assertRaisesRegex(EvidenceError, "Review-Gates do not match"):
            validate(
                approved_snapshot(),
                "TASK-1",
                base=BASE,
                head=HEAD,
                package=PACKAGE,
                required_gates=("security",),
            )


if __name__ == "__main__":
    unittest.main()
