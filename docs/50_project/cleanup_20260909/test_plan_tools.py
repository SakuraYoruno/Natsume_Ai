"""Offline tests for the planning tools only; no application imports or IO effects."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

import check_plan


class PlanToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((Path(__file__).parent / "tasks.json").read_text(encoding="utf-8"))
        cls.tasks = {task["id"]: task for task in cls.plan["tasks"]}

    def test_actual_manifest_paths_and_dependencies(self):
        self.assertEqual([], check_plan.validate(self.plan))

    def test_dependency_cycle_is_rejected(self):
        plan = copy.deepcopy(self.plan)
        plan["tasks"][0]["depends_on"].append(plan["tasks"][0]["id"])
        self.assertTrue(any("cycle" in e for e in check_plan.validate(plan)))

    def test_protected_sql_mutation_is_rejected(self):
        plan = copy.deepcopy(self.plan)
        task = next(t for t in plan["tasks"] if t["id"] == "C01")
        task["create"].append("host_brain/audit/migrations/9999_unapproved.sql")
        self.assertTrue(any("protected" in e for e in check_plan.validate(plan)))

    def test_hold_cannot_carry_mutation_scope(self):
        plan = copy.deepcopy(self.plan)
        task = next(t for t in plan["tasks"] if t["state"] == "HOLD")
        task["edit"].append("host_brain/pentest/autonomous_kernel.py")
        self.assertTrue(any("non-READY" in e for e in check_plan.validate(plan)))

    def test_unsafe_paths_are_rejected(self):
        for value in ("", "../a.py", "/a.py", "C:/a.py", "a\\b.py", "a/*.py"):
            with self.subTest(value=value):
                self.assertFalse(check_plan.safe_path(value))
        self.assertTrue(check_plan.safe_path("host_brain/tools/__init__.py"))

    def test_allowed_artifact_changes(self):
        task = self.tasks["C04"]
        changes = [("M", path) for path in task["edit"]]
        changes += [("?", path) for path in task["create"]]
        self.assertEqual([], check_plan.check_scope(task, changes))

    def test_out_of_scope_and_unapproved_deletion(self):
        task = self.tasks["C04"]
        self.assertTrue(any("OUT_OF_SCOPE" in e for e in check_plan.check_scope(task, [("M", "host_brain/pentest/action_policy.py")])))
        errors = check_plan.check_scope(task, [("D", task["edit"][0])])
        self.assertTrue(any("DELETE_NOT_AUTHORIZED" in e for e in errors))

    def test_evidence_task_cannot_edit_runtime(self):
        errors = check_plan.check_scope(self.tasks["G00"], [("M", "host_brain/app/asgi.py")])
        self.assertIn("TASK_HAS_NO_CODE_MUTATION_AUTHORITY", errors)

    def test_plan_receipt_does_not_count_as_business_change(self):
        changes = [("?", check_plan.PLAN_PREFIX + "receipts/G00.md")]
        self.assertEqual([], check_plan.check_scope(self.tasks["G00"], changes))

    def test_all_conditional_candidates_have_exact_task(self):
        baseline = json.loads((Path(__file__).parent / "baseline.json").read_text(encoding="utf-8"))
        expected = {(r["path"], r["symbol"]) for r in baseline["conditional_tail_candidates"]}
        actual = {(t["candidate_edit"][0], symbol) for t in self.plan["tasks"] if t["kind"] == "conditional_migration" for symbol in t["candidate_symbols"]}
        self.assertEqual(expected, actual)
        self.assertEqual(223, len(expected))


if __name__ == "__main__":
    unittest.main(verbosity=2)
