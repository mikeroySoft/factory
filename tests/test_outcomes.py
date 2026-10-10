"""Versioned owner outcome attestations stay attributed, bounded, and revision-bound."""
from __future__ import annotations

import unittest
from pathlib import Path

from factory import binding, config, outcomes

REPO = "example/project"
AT = "2026-10-09T12:00:00Z"


def initiative(number: int = 50, *, owner: str = "@alice", plan: str = "Ship safely") -> dict:
    body = f"""**Status**
underway

**Outcome**
A useful result.

**Owner**
{owner}

**Areas**
factory/outcomes.py

**Boundaries**
No deployment authority.

**Plan**
{plan}

**Open decisions**
None.

**Success evidence**
Owner evidence linked to a source revision.

**Implementation links**
"""
    return {
        "number": number,
        "title": "Outcome control",
        "state": "open",
        "body": body,
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "labels": [{"name": "initiative"}],
        "comments": 0,
    }


def details(number: int = 50, *, kind: str = "accepted", summary: str = "Useful in practice.") -> dict:
    return {
        "op": "outcome",
        "number": number,
        "kind": kind,
        "source_revision": "eacb791",
        "evidence_url": f"https://github.com/{REPO}/actions/runs/7",
        "summary": summary,
    }


def comment(comment_id: int, body: str, *, author: str = "alice", created: str = AT,
            updated: str | None = None, url: str | None = None) -> dict:
    return {
        "id": comment_id,
        "body": body,
        "user": {"login": author},
        "created_at": created,
        "updated_at": created if updated is None else updated,
        "html_url": url or f"https://github.com/{REPO}/issues/50#issuecomment-{comment_id}",
    }


class OutcomeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = config.Config(Path("/tmp"), REPO)
        self.issue = initiative()

    def render(self, **changes) -> str:
        request = details()
        request.update(changes)
        return outcomes.format_comment(self.cfg, self.issue, "alice", request)

    def test_formatter_binds_owner_issue_and_current_canonical_revision(self) -> None:
        body = self.render(kind="released", summary="Release 0.4 is available.")
        projected = outcomes.project(self.cfg, self.issue, [comment(11, body)])
        revision = binding.from_issue(self.cfg, 50, self.issue)["sha256"]

        self.assertTrue(body.startswith("<!-- factory-outcome:v1 -->\n\n```json\n"))
        self.assertEqual(projected["canonical_revision"], revision)
        self.assertEqual(projected["owner"], "alice")
        self.assertEqual(projected["status"], "unknown")
        self.assertEqual(projected["attributed"]["released"]["status"], "attested")
        self.assertEqual(projected["attributed"]["accepted"]["status"], "unknown")
        self.assertEqual(projected["verified"], {
            "deployment": {"status": "unknown", "evidence": []},
            "health": {"status": "unknown", "evidence": []},
        })

        reassigned = outcomes.project(
            self.cfg,
            initiative(owner="@bob"),
            [comment(11, body)],
        )
        self.assertEqual(reassigned["canonical_revision"], revision)
        self.assertEqual(reassigned["evidence"][0]["status"], "foreign_owner")
        self.assertEqual(reassigned["attributed"]["released"]["status"], "unknown")

        cases = [
            ("unauthorized_actor", self.issue, "mallory", details()),
            ("invalid_owner", initiative(owner="@alice\n@mallory"), "alice", details()),
            ("invalid_owner", {**self.issue, "body": self.issue["body"] + "\n**Owner**\n@alice\n"}, "alice", details()),
            ("invalid_issue", {**self.issue, "pull_request": {"url": "x"}}, "alice", details()),
            ("invalid_details", self.issue, "alice", {**details(), "extra": True}),
            ("invalid_details", self.issue, "alice", {**details(), "number": 51}),
            ("invalid_kind", self.issue, "alice", {**details(), "kind": "deployed"}),
            ("invalid_source_revision", self.issue, "alice", {**details(), "source_revision": "bad revision"}),
            ("invalid_evidence_url", self.issue, "alice", {**details(), "evidence_url": "javascript:alert(1)"}),
            ("invalid_summary", self.issue, "alice", {**details(), "summary": ""}),
        ]
        for code, raw_issue, actor, request in cases:
            with self.subTest(code=code), self.assertRaises(outcomes.OutcomeError) as caught:
                outcomes.format_comment(self.cfg, raw_issue, actor, request)
            self.assertEqual(caught.exception.code, code)

    def test_revision_owner_edit_and_source_identity_fail_closed(self) -> None:
        body = self.render()
        old = self.issue
        current = initiative(plan="A changed canonical plan")
        copied = outcomes.format_comment(self.cfg, initiative(51), "alice", details(51))
        rows = [
            comment(1, body),
            comment(2, body, author="mallory"),
            comment(3, body, updated="2026-10-09T12:01:00Z"),
            comment(4, body, url=f"https://github.com/{REPO}/issues/50#issuecomment-999"),
            comment(5, body + "\nnon-canonical suffix"),
            comment(6, copied),
        ]
        projected = outcomes.project(self.cfg, current, rows)

        self.assertNotEqual(
            binding.from_issue(self.cfg, 50, old)["sha256"],
            binding.from_issue(self.cfg, 50, current)["sha256"],
        )
        self.assertEqual(projected["status"], "unknown")
        self.assertEqual(projected["attributed"]["accepted"]["status"], "unknown")
        self.assertEqual(
            [item["status"] for item in projected["evidence"]],
            ["stale", "foreign_owner", "edited", "invalid", "invalid", "invalid"],
        )
        self.assertEqual(projected["evidence"][0]["reason"], "canonical_revision_changed")
        self.assertEqual(projected["evidence"][3]["reason"], "comment_url_invalid")
        self.assertEqual(projected["evidence"][5]["reason"], "initiative_mismatch")

        conflicting = outcomes.project(
            self.cfg,
            self.issue,
            [comment(7, body), comment(7, body)],
        )
        self.assertEqual(
            [item["reason"] for item in conflicting["evidence"]],
            ["comment_identity_conflict", "comment_identity_conflict"],
        )
        self.assertEqual(conflicting["coverage"]["status"], "partial")
        self.assertEqual(conflicting["attributed"]["accepted"]["status"], "unknown")

    def test_latest_each_kind_supersedes_only_that_owner_report(self) -> None:
        first = self.render(summary="Initial acceptance.")
        second = self.render(summary="Acceptance after observation.")
        released = self.render(kind="released", summary="Release published.")
        projected = outcomes.project(self.cfg, self.issue, [
            comment(10, first, created="2026-10-09T10:00:00Z"),
            comment(12, released, created="2026-10-09T10:30:00Z"),
            comment(11, second, created="2026-10-09T11:00:00Z"),
        ])

        self.assertEqual(projected["status"], "owner_attested")
        self.assertEqual(projected["attributed"]["accepted"]["evidence"]["id"], 11)
        self.assertEqual(projected["attributed"]["released"]["evidence"]["id"], 12)
        self.assertEqual([item["status"] for item in projected["evidence"]],
                         ["superseded", "current", "current"])
        self.assertEqual(projected["attributed"]["installed"]["status"], "unknown")
        self.assertEqual(projected["attributed"]["healthy"]["status"], "unknown")

    def test_edited_later_owner_record_does_not_resurrect_old_acceptance(self) -> None:
        first = comment(10, self.render(), created="2026-10-09T10:00:00Z")
        changed = comment(11, self.render(), created="2026-10-09T11:00:00Z",
                          updated="2026-10-09T12:00:00Z")
        projected = outcomes.project(self.cfg, self.issue, [first, changed])
        self.assertEqual(projected["attributed"]["accepted"]["status"], "unknown")
        self.assertEqual(projected["evidence"][0]["reason"], "later_owner_record_unusable")
        renewed = comment(12, self.render(), created="2026-10-09T13:00:00Z")
        projected = outcomes.project(self.cfg, self.issue, [first, changed, renewed])
        self.assertEqual(projected["attributed"]["accepted"]["evidence"]["id"], 12)
        self.assertEqual(projected["attributed"]["accepted"]["status"], "attested")

    def test_partial_or_missing_comment_source_never_establishes_current_acceptance(self) -> None:
        body = self.render()
        partial = outcomes.project(self.cfg, self.issue, [comment(1, body)], complete=False)
        missing = outcomes.project(self.cfg, self.issue, None, complete=False)

        self.assertEqual(partial["coverage"]["status"], "partial")
        self.assertEqual(partial["evidence"][0]["status"], "current")
        self.assertEqual(partial["attributed"]["accepted"], {
            "status": "unknown",
            "evidence": partial["evidence"][0],
            "reason": "comment_coverage_partial",
        })
        self.assertEqual(partial["status"], "unknown")
        self.assertEqual(missing["coverage"]["status"], "unavailable")
        self.assertEqual(missing["evidence"], [])
        self.assertTrue(all(value["status"] == "unknown" for value in missing["attributed"].values()))


if __name__ == "__main__":
    unittest.main()
