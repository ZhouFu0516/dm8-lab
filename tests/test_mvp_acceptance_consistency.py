"""Offline acceptance invariants; no database or VM boundary is executed."""
import ast
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from dm8_mvp import restore
from dm8_mvp.runner import GuestResult
import test_mvp_restore_status as status_fixtures
import test_mvp_restore_reporting as report_fixtures


PHYSICAL = "MVP_PHYSICAL_RECOVERY_PASS"
VERIFY = "STAGE START-VERIFY"
FINISHED = "MVP_RECOVERY_STAGES_PASS"


def complete_output():
    return PHYSICAL + "\n" + VERIFY + "\n" + status_fixtures.bounded_real_output() + "\n" + FINISHED + "\n"


def run_r1(output, exitcode=0, exited=True, stderr=""):
    with tempfile.TemporaryDirectory() as td:
        config = dict(guest="dm8-restore-c", backupset="/data/mvp60/i1-input/offline",
                      target="/data/mvp60/OFFLINE", mapping="/data/mvp60/offline-map.txt",
                      instance_name="DM8RT01", report_dir=td, run_id="offline-acceptance")
        path = Path(td) / "config.json"
        path.write_text(json.dumps(config))
        reply = GuestResult(exitcode, output, stderr, 7, exited)
        with patch("dm8_mvp.restore.QgaRunner.run", return_value=reply) as guest, redirect_stdout(io.StringIO()):
            rc = restore.main(["--config", str(path), "--execute"])
        report = json.loads((Path(td) / "offline-acceptance.json").read_text())
        assert guest.call_count == 1
        return rc, report


class AcceptanceConsistencyTests(unittest.TestCase):
    def test_complete_evidence_pass_implies_both_stages_pass(self):
        rc, report = run_r1(complete_output())
        self.assertEqual(rc, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["stages"], dict(physical_recovery="PASS", verification="PASS"))
        self.assertEqual(report["counts"], status_fixtures.COUNTS)

    def test_missing_any_required_marker_cannot_pass(self):
        for marker in (PHYSICAL, VERIFY, FINISHED):
            with self.subTest(marker=marker):
                rc, report = run_r1(complete_output().replace(marker + "\n", ""))
                self.assertNotEqual(rc, 0)
                self.assertNotEqual(report["status"], "PASS")
                self.assertIsNone(report["counts"])

    def test_duplicate_markers_cannot_pass(self):
        for marker in (PHYSICAL, VERIFY, FINISHED):
            with self.subTest(marker=marker):
                rc, report = run_r1(complete_output().replace(marker, marker + "\n" + marker))
                self.assertEqual(rc, 3)
                self.assertEqual(report["status"], "INCOMPLETE")

    def test_wrong_marker_order_cannot_pass(self):
        for output in (
            complete_output().replace(PHYSICAL, "TEMP").replace(VERIFY, PHYSICAL).replace("TEMP", VERIFY),
            complete_output().replace(VERIFY, "TEMP").replace(FINISHED, VERIFY).replace("TEMP", FINISHED),
        ):
            with self.subTest(output=output):
                rc, report = run_r1(output)
                self.assertEqual(rc, 3)
                self.assertEqual(report["status"], "INCOMPLETE")

    def test_markers_in_other_text_cannot_supply_evidence(self):
        for marker in (PHYSICAL, VERIFY, FINISHED):
            for decorated in ("prefix " + marker, marker + " suffix"):
                with self.subTest(marker=decorated):
                    rc, report = run_r1(complete_output().replace(marker, decorated))
                    self.assertNotEqual(rc, 0)
                    self.assertNotEqual(report["status"], "PASS")

    def test_query_outside_verification_stage_cannot_supply_state_or_counts(self):
        output = status_fixtures.bounded_real_output() + "\n" + PHYSICAL + "\n" + VERIFY + "\n" + FINISHED
        rc, report = run_r1(output)
        self.assertEqual(rc, 3)
        self.assertNotEqual(report["status"], "PASS")

    def test_nonzero_or_unknown_guest_cannot_pass_despite_complete_output(self):
        for exitcode, exited, expected in ((10, True, "FAIL"), (None, False, "BLOCKED"), (0, False, "BLOCKED")):
            with self.subTest(exitcode=exitcode, exited=exited):
                rc, report = run_r1(complete_output(), exitcode=exitcode, exited=exited)
                self.assertNotEqual(rc, 0)
                self.assertEqual(report["status"], expected)

    def test_complete_markers_do_not_override_bad_state_or_count(self):
        for old, new in (("DM8RT01       OPEN    NORMAL", "DM8RT01 MOUNT NORMAL"),
                         ("__COUNT_JOBS__", "missing COUNT marker"),
                         ("1          112", "1          invalid")):
            with self.subTest(new=new):
                rc, report = run_r1(complete_output().replace(old, new))
                self.assertNotEqual(rc, 0)
                self.assertNotEqual(report["status"], "PASS")

    def test_global_sql_errors_before_state_and_between_counts_are_detected(self):
        for before in ("__R1_STATUS_BEGIN__", "__COUNT_JOBS__"):
            output = complete_output().replace(before, "DM[-2007]: failed statement\n" + before)
            with self.subTest(before=before):
                # State shape is still valid; exact diagnostic proves the global check ran.
                with self.assertRaisesRegex(ValueError, "DIsql reported a SQL error"):
                    restore.parse_restore_status(output, "DM8RT01")
                with self.assertRaisesRegex(ValueError, "DIsql reported a SQL error"):
                    restore.parse_count_output(output)
                rc, report = run_r1(output)
                self.assertNotEqual(rc, 0)
                self.assertIn("DIsql reported a SQL error", report["stderr"])

    def test_global_sql_error_after_completion_or_in_stderr_is_not_ignored(self):
        for output, stderr in ((complete_output() + "DM[-2007]\n", ""), (complete_output(), "DISQL-2007\n")):
            with self.subTest(stderr=stderr):
                rc, report = run_r1(output, stderr=stderr)
                self.assertNotEqual(rc, 0)
                self.assertNotEqual(report["status"], "PASS")

    def test_generated_gate_preserves_diagnostics_under_errexit(self):
        config = dict(guest="dm8-restore-c", backupset="/data/mvp60/input/offline",
                      target="/data/mvp60/OFFLINE", mapping="/data/mvp60/offline-map.txt")
        script = restore.build_script(config)
        self.assertTrue(script.startswith("set -u\n"))
        self.assertNotIn("set -e", script)
        gate = restore.build_verification_gate()
        self.assertIn(gate, script)
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            env.pop("PYTHONPATH", None)
            for options in ("set -u", "set -eu"):
                for valid in (False, True):
                    with self.subTest(options=options, valid=valid):
                        output = status_fixtures.bounded_real_output()
                        if not valid:
                            output = "DM[-2007]: OFFLINE_FAILURE_DIAGNOSTIC\n" + output
                        (Path(td) / "verify.log").write_text(output)
                        header = options + f"\nLOGDIR={shlex.quote(td)}\nINSTANCE_NAME=DM8RT01\n"
                        result = subprocess.run(["bash", "-c", header + gate + "\necho GATE_PASS\n"],
                                                cwd=td, env=env, capture_output=True, text=True, timeout=10)
                        self.assertEqual(result.returncode, 0 if valid else 10)
                        if valid:
                            self.assertIn("GATE_PASS", result.stdout)
                        else:
                            self.assertIn("FAIL:R1 verification: DIsql reported a SQL error", result.stdout)
                            self.assertIn("OFFLINE_FAILURE_DIAGNOSTIC", result.stdout)
                            self.assertIn("FAIL:R1 state or COUNT verification", result.stdout)
                            self.assertNotIn("GATE_PASS", result.stdout)

    def test_generated_completion_footer_separates_marker_after_real_prompt_tail(self):
        with tempfile.TemporaryDirectory() as td:
            logdir = Path(td)
            (logdir / "verify.log").write_text(status_fixtures.bounded_real_output() + "\nSQL> ")
            env = dict(os.environ, LOGDIR=td)
            result = subprocess.run(["bash", "-c", restore.build_completion_footer()],
                                    capture_output=True, text=True, env=env, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            captured = PHYSICAL + "\n" + VERIFY + "\n" + result.stdout
            rc, report = run_r1(captured)
            self.assertEqual(rc, 0)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["counts"], status_fixtures.COUNTS)

    def test_embedded_python_has_no_package_import_and_python37_syntax(self):
        gate = restore.build_verification_gate()
        embedded = gate.split("<<'PY_R1_VERIFY'\n", 1)[1].split("\nPY_R1_VERIFY", 1)[0]
        tree = ast.parse(embedded, feature_version=(3, 7))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertEqual(imports, ["__future__"])
        self.assertNotIn("dm8_mvp", embedded)
        self.assertNotIn("dm8_verify", embedded)


class I1AcceptanceConsistencyTests(unittest.TestCase):
    def setUp(self):
        report_fixtures.RestoreReportingTests.setUp(self)

    def test_i1_rejects_each_contradictory_r1_pass_without_final_comparison(self):
        cases = [None, {}, dict(physical_recovery="PASS", verification="FAIL"),
                 dict(physical_recovery="NOT EXECUTED", verification="PASS"),
                 dict(physical_recovery="BLOCKED", verification="PASS"),
                 dict(physical_recovery="PASS", verification="INCOMPLETE")]
        for stages in cases:
            with self.subTest(stages=stages):
                def contradictory(cfg):
                    directory = Path(cfg["r1_report_dir"])
                    directory.mkdir(parents=True)
                    report = dict(run_id=cfg["run_id"], status="PASS", exitcode=0,
                                  verification="ROW_COUNT_OBSERVED", comparison="NOT EXECUTED",
                                  counts=status_fixtures.COUNTS)
                    if stages is not None:
                        report["stages"] = stages
                    (directory / (cfg["run_id"] + ".json")).write_text(json.dumps(report))
                    return 0
                rc, report, comparisons = report_fixtures.RestoreReportingTests.run_workflow(self, substitute=contradictory)
                self.assertEqual(rc, 3)
                self.assertEqual(report["status"], "INCOMPLETE")
                self.assertEqual(report["stages"]["restore"], "INCOMPLETE")
                self.assertEqual(report["stages"]["row_count_compare"], "NOT EXECUTED")
                self.assertEqual(comparisons, 1)

    def test_complete_r1_success_still_uses_existing_final_comparator(self):
        rc, report, comparisons = report_fixtures.RestoreReportingTests.run_workflow(self)
        self.assertEqual(rc, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["r1_report"]["stages"], dict(physical_recovery="PASS", verification="PASS"))
        self.assertEqual(report["restored_comparison"]["status"], "PASS")
        self.assertEqual(comparisons, 2)
