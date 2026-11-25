"""Exercise actual R1 -> I1 reporting and the existing final comparator offline."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch

from dm8_mvp import integration, restore
from dm8_mvp.runner import GuestResult, QgaUnknown
import test_mvp_integration as fixtures
import test_mvp_restore_status as status_fixtures


class RestoreReportingTests(unittest.TestCase):
    def setUp(self):
        fixtures.I1IntegrationTests.setUp(self)

    def run_workflow(self, guest_result=None, unknown=False, substitute=None):
        counts = status_fixtures.COUNTS
        baseline = integration.restored_count_baseline(counts)
        baseline["source_type"] = "DATABASE"
        digest = "a" * 64
        primary = dict(status="PASS", stages=dict(backup="PASS", check="PASS"),
                       manifest="a.bak 3\na.meta 2",
                       hashes=f"{digest}  /bak/a.bak\n{digest}  /bak/a.meta")
        if guest_result is None:
            guest_result = GuestResult(0, "STAGE RESTORE\nSTAGE RECOVER\nMVP_PHYSICAL_RECOVERY_PASS\n"
                                       "STAGE START-VERIFY\n" + status_fixtures.bounded_real_output() +
                                       "\nMVP_RECOVERY_STAGES_PASS\n", "", 81, True)
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            path = Path(td) / "config.json"
            path.write_text(json.dumps(dict(self.cfg, report_dir=td)))
            stack.enter_context(patch("dm8_mvp.integration.invoke_guest", return_value=dict(status="PASS", available_bytes=99999)))
            stack.enter_context(patch("dm8_mvp.integration.collect_source_baseline", return_value=baseline))
            stack.enter_context(patch("dm8_mvp.integration.execute_primary", return_value=GuestResult(0, json.dumps(primary), "", 1, True)))
            stack.enter_context(patch("dm8_mvp.integration.prepare_restore_input", return_value=dict(status="PASS")))
            stack.enter_context(patch("dm8_mvp.integration.transfer_manifest", return_value=[dict(name="a.bak"), dict(name="a.meta")]))
            stack.enter_context(patch("dm8_mvp.integration.verify_target_hashes", return_value={"a.bak": digest, "a.meta": digest}))
            stack.enter_context(patch("dm8_mvp.restore.QgaRunner.run", return_value=guest_result,
                                      side_effect=QgaUnknown("outcome unknown; no retry") if unknown else None))
            if substitute is not None:
                stack.enter_context(patch("dm8_mvp.integration.execute_r1", side_effect=substitute))
            comparator = stack.enter_context(patch("dm8_mvp.integration.compare_row_count_baselines",
                                                   wraps=integration.compare_row_count_baselines))
            stack.enter_context(redirect_stdout(io.StringIO()))
            rc = integration.main(["--config", str(path), "--execute"])
            report = json.loads(next(Path(td).glob("*-i1.json")).read_text())
            return rc, report, comparator.call_count

    def test_actual_r1_success_reaches_actual_final_comparator(self):
        rc, report, comparisons = self.run_workflow()
        self.assertEqual(rc, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(comparisons, 2)
        self.assertTrue(all(value == "PASS" for value in report["stages"].values()))
        self.assertEqual(report["restored_comparison"]["status"], "PASS")
        self.assertEqual(report["r1_report"]["counts"], status_fixtures.COUNTS)
        self.assertEqual(report["r1_report"]["stages"], dict(physical_recovery="PASS", verification="PASS"))

    def test_r1_acceptance_fail_propagates_fail_with_physical_success(self):
        output = ("STAGE RESTORE\nSTAGE RECOVER\nMVP_PHYSICAL_RECOVERY_PASS\nSTAGE START-VERIFY\n" +
                  status_fixtures.FIXTURE.read_text() + "\nFAIL:database not OPEN/NORMAL\n")
        rc, report, comparisons = self.run_workflow(GuestResult(10, output, "", 81, True))
        self.assertEqual(rc, 1)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["stages"]["restore"], "FAIL")
        self.assertEqual(report["restore_exitcode"], 10)
        self.assertEqual(report["r1_report"]["exitcode"], 10)
        self.assertEqual(report["r1_report"]["stages"], dict(physical_recovery="PASS", verification="FAIL"))
        self.assertEqual(report["stages"]["row_count_compare"], "NOT EXECUTED")
        self.assertEqual(comparisons, 1)

    def test_physical_restore_failure_is_distinct_from_verification_failure(self):
        rc, report, comparisons = self.run_workflow(GuestResult(10, "STAGE RESTORE\nFAIL:restore\n", "", 81, True))
        self.assertEqual(rc, 1)
        self.assertEqual(report["stages"]["restore"], "FAIL")
        self.assertEqual(report["r1_report"]["stages"], dict(physical_recovery="FAIL", verification="NOT EXECUTED"))
        self.assertEqual(comparisons, 1)

    def test_incomplete_r1_evidence_propagates_without_final_comparison(self):
        output = ("MVP_PHYSICAL_RECOVERY_PASS\nSTAGE START-VERIFY\n" +
                  status_fixtures.bounded_real_output().replace("__COUNT_JOBS__", "missing") +
                  "\nMVP_RECOVERY_STAGES_PASS\n")
        rc, report, comparisons = self.run_workflow(GuestResult(0, output, "", 81, True))
        self.assertEqual(rc, 3)
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["stages"]["restore"], "INCOMPLETE")
        self.assertEqual(report["stages"]["row_count_compare"], "NOT EXECUTED")
        self.assertEqual(comparisons, 1)

    def test_qga_unknown_remains_blocked_without_final_comparison(self):
        rc, report, comparisons = self.run_workflow(unknown=True)
        self.assertEqual(rc, 2)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["stages"]["restore"], "BLOCKED")
        self.assertIsNone(report["r1_report"]["exitcode"])
        self.assertEqual(report["r1_report"]["stages"], dict(physical_recovery="BLOCKED", verification="BLOCKED"))
        self.assertEqual(comparisons, 1)

    def test_missing_this_run_report_cannot_use_historical_pass(self):
        def missing(cfg):
            report_dir = Path(cfg["report_dir"])
            (report_dir / "r1-old.json").write_text(json.dumps(dict(status="PASS", run_id="old")))
            return 0
        rc, report, comparisons = self.run_workflow(substitute=missing)
        self.assertEqual(rc, 1)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["stages"]["restore"], "FAIL")
        self.assertIn("this-run report missing", report["failure"]["reason"])
        self.assertEqual(comparisons, 1)

    def test_mismatched_run_report_is_fail_not_initial_blocked(self):
        def wrong_run(cfg):
            directory = Path(cfg["r1_report_dir"])
            directory.mkdir(parents=True)
            (directory / (cfg["run_id"] + ".json")).write_text(json.dumps(dict(status="PASS", run_id="old")))
            return 0
        rc, report, comparisons = self.run_workflow(substitute=wrong_run)
        self.assertEqual(rc, 1)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["stages"]["restore"], "FAIL")
        self.assertIn("run_id mismatch", report["failure"]["reason"])
        self.assertEqual(comparisons, 1)

    def test_nonzero_r1_cannot_pass_even_if_report_claims_pass(self):
        def contradictory(cfg):
            directory = Path(cfg["r1_report_dir"])
            directory.mkdir(parents=True)
            (directory / (cfg["run_id"] + ".json")).write_text(json.dumps(dict(status="PASS", run_id=cfg["run_id"])))
            return 10
        rc, report, comparisons = self.run_workflow(substitute=contradictory)
        self.assertEqual(rc, 1)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["stages"]["restore"], "FAIL")
        self.assertEqual(comparisons, 1)
