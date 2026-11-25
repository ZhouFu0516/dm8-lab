"""Offline regressions for the first real I1 BACKUP-output incompatibility."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from dm8_mvp import guest_runtime, integration
from dm8_mvp.runner import GuestResult, QgaUnknown


REAL_BACKUP_OUTPUT = """disql V8
SQL> SQL> SQL> SQL> SQL> SQL> SQL> SQL>
Server[192.168.241.11:5236]:mode is primary, state is open
login used time : 1.788(ms)
used time: 00:00:06.421. Execute id is 2501.
"""
PREFLIGHT_OUTPUT = "__B1_INSTANCE__\n1 DM8RT01|OPEN|PRIMARY\n__B1_ARCHIVE__\n1 Y\n"
REPO = Path(__file__).parents[1]


class BackupAcceptanceTests(unittest.TestCase):
    def primary_result(self, sql_output=REAL_BACKUP_OUTPUT, sql_exitcode=0,
                       check_exitcode=0, check_output="check backupset successfully\n",
                       check_exception=None, include_meta=True):
        """Run real primary(); replace only SQL/OS tools with local fixtures."""
        with tempfile.TemporaryDirectory() as td:
            backup = Path(td) / "backup-root" / "run"
            request = dict(backupset=str(backup), logdir=str(Path(td) / "logs" / "run"),
                           preflight_queries=integration.preflight_queries())
            calls = []

            def sql(query):
                if query["queries"] == request["preflight_queries"]:
                    return dict(status="PASS", exitcode=0, stdout=PREFLIGHT_OUTPUT, stderr="")
                self.assertEqual(query["queries"], [f"BACKUP DATABASE FULL BACKUPSET '{backup}';"])
                backup.mkdir()
                (backup / "part.bak").write_bytes(b"offline backup fixture")
                if include_meta:
                    (backup / "part.meta").write_bytes(b"offline metadata fixture")
                return dict(status="PASS" if sql_exitcode == 0 else "FAIL", exitcode=sql_exitcode,
                            stdout=sql_output, stderr="")

            def command(argv, **kwargs):
                calls.append(argv)
                self.assertEqual(argv[:4], ["runuser", "-u", "dmdba", "--"])
                if argv[4] == "mkdir":
                    Path(argv[-1]).mkdir(parents=True, exist_ok=True)
                    return subprocess.CompletedProcess(argv, 0)
                self.assertEqual(argv[4], "/opt/dmdbms/bin/dmrman")
                self.assertEqual(argv[-1], f"CTLSTMT=CHECK BACKUPSET '{backup}' LEVEL 1")
                self.assertTrue((backup / "part.bak").is_file())
                self.assertTrue((backup / "part.meta").is_file())
                if check_exception:
                    raise check_exception
                return subprocess.CompletedProcess(argv, check_exitcode, check_output, "")

            with patch.object(guest_runtime, "sql_result", side_effect=sql), \
                 patch.object(guest_runtime, "probe", return_value={"status": "PASS"}), \
                 patch.object(guest_runtime.subprocess, "run", side_effect=command):
                result = guest_runtime.primary(request)
            return result, calls

    def i1_report(self, primary, guest_exitcode=0):
        """Run the actual CLI function with every real guest boundary mocked."""
        with tempfile.TemporaryDirectory() as td:
            cfg = json.loads((REPO / "config/mvp-b1.example.json").read_text())
            cfg["report_dir"] = td
            path = Path(td) / "config.json"
            path.write_text(json.dumps(cfg))
            counts = {name: 1 for name in ("LOCATIONS", "DEPARTMENTS", "JOBS", "EMPLOYEES", "JOB_HISTORY", "SALARIES")}
            baseline = integration.restored_count_baseline(counts)
            baseline["source_type"] = "DATABASE"

            def restore(run):
                report_dir = Path(run["r1_report_dir"])
                report_dir.mkdir(parents=True)
                (report_dir / f"{run['run_id']}.json").write_text(json.dumps(dict(
                    run_id=run["run_id"], status="PASS", verification="ROW_COUNT_OBSERVED",
                    comparison="NOT EXECUTED", counts=counts,
                    stages=dict(physical_recovery="PASS", verification="PASS"))))
                return 0

            reply = None if isinstance(primary, Exception) else GuestResult(
                guest_exitcode, json.dumps(primary), "", 81, True)
            with patch.object(integration, "invoke_guest", return_value={"status": "PASS", "available_bytes": 10**12}), \
                 patch.object(integration, "collect_source_baseline", return_value=baseline), \
                 patch.object(integration, "execute_primary", return_value=reply,
                              side_effect=primary if isinstance(primary, Exception) else None), \
                 patch.object(integration, "prepare_restore_input", return_value={"status": "PASS"}), \
                 patch.object(integration, "transfer_manifest", return_value=[]) as transfer, \
                 patch.object(integration, "verify_target_hashes", return_value={}), \
                 patch.object(integration, "execute_r1", side_effect=restore) as r1, \
                 redirect_stdout(io.StringIO()):
                rc = integration.main(["--config", str(path), "--execute"])
            report = json.loads(next(Path(td).glob("*-i1.json")).read_text())
            return rc, report, transfer.call_count, r1.call_count

    def test_real_output_enters_check_and_normal_pipeline_continues(self):
        self.assertNotIn("executed successfully", REAL_BACKUP_OUTPUT.lower())
        result, calls = self.primary_result()
        self.assertEqual(len(calls), 2)  # local mkdir stub, then CHECK stub
        self.assertEqual(result["backup"]["exitcode"], 0)
        self.assertEqual(result["backup"]["stdout"], REAL_BACKUP_OUTPUT)
        self.assertTrue(result["backup"]["executed"])
        self.assertEqual(result["check"]["exitcode"], 0)
        self.assertEqual(result["stages"], {"backup": "PASS", "check": "PASS"})
        self.assertEqual(result["status"], "PASS")
        rc, report, transfer, restore = self.i1_report(result)
        self.assertEqual(rc, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual((transfer, restore), (1, 1))

    def test_sql_error_and_nonzero_exit_stop_before_check_and_transfer(self):
        for output, code in (("[-2007]: Syntax error\n", 0), (REAL_BACKUP_OUTPUT, 9)):
            with self.subTest(exitcode=code):
                result, calls = self.primary_result(sql_output=output, sql_exitcode=code)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result["stages"], {"backup": "FAIL", "check": "NOT EXECUTED"})
                rc, report, transfer, restore = self.i1_report(result)
                self.assertEqual(rc, 1)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["primary_result"]["backup"]["exitcode"], code)
                self.assertEqual((transfer, restore), (0, 0))

    def test_completed_backup_without_check_records_incomplete_and_sql_evidence(self):
        result, calls = self.primary_result(include_meta=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["stages"], {"backup": "INCOMPLETE", "check": "NOT EXECUTED"})
        rc, report, transfer, restore = self.i1_report(result)
        self.assertNotEqual(rc, 0)
        self.assertEqual(report["stages"]["backup"], "INCOMPLETE")
        self.assertEqual(report["stages"]["check"], "NOT EXECUTED")
        self.assertEqual(report["primary_result"]["backup"]["stdout"], REAL_BACKUP_OUTPUT)
        self.assertEqual(report["primary_result"]["backup"]["exitcode"], 0)
        self.assertEqual(report["primary_execution"]["exitcode"], 0)
        self.assertEqual(report["primary_execution"]["pid"], 81)
        self.assertEqual((transfer, restore), (0, 0))
        # Failure to spawn CHECK is known not-executed, not a timed-out CHECK.
        result, calls = self.primary_result(check_exception=FileNotFoundError("offline missing dmrman"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["stages"], {"backup": "INCOMPLETE", "check": "NOT EXECUTED"})
        self.assertFalse(result["check"]["executed"])
        rc, report, transfer, restore = self.i1_report(result)
        self.assertEqual(rc, 2)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["failure"]["stage"], "check")
        self.assertEqual((transfer, restore), (0, 0))

    def test_check_failure_or_unknown_stops_with_actual_stage_report(self):
        cases = [(9, "check backupset successfully\n", None, "FAIL"),
                 (0, "DM[-8287]: check failed\n", None, "FAIL"),
                 (0, "file opened successfully\n", None, "FAIL"),
                 (0, "", subprocess.TimeoutExpired("mock-dmrman", 90), "BLOCKED")]
        for code, output, error, expected in cases:
            with self.subTest(exitcode=code, output=output, timeout=bool(error)):
                result, calls = self.primary_result(check_exitcode=code, check_output=output, check_exception=error)
                self.assertEqual(len(calls), 2)
                rc, report, transfer, restore = self.i1_report(result)
                self.assertEqual(rc, 2 if error else 1)
                self.assertEqual(report["status"], expected)
                self.assertEqual(report["stages"]["check"], expected)
                self.assertNotEqual(report["stages"]["backup"], "NOT EXECUTED")
                self.assertEqual(report["primary_result"]["backup"]["exitcode"], 0)
                self.assertEqual(report["primary_result"]["check"]["exitcode"], None if error else code)
                self.assertEqual(report["failure"]["stage"], "check")
                self.assertEqual((transfer, restore), (0, 0))
                if error:
                    self.assertIn("UNKNOWN", report["failure"]["guest_outcome"])

    def test_lost_primary_qga_result_is_not_reported_as_unexecuted(self):
        rc, report, transfer, restore = self.i1_report(QgaUnknown("offline unknown outcome"))
        self.assertEqual(rc, 2)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["stages"]["backup"], "BLOCKED")
        self.assertEqual(report["stages"]["check"], "BLOCKED")
        self.assertIn("UNKNOWN", report["failure"]["guest_outcome"])
        self.assertEqual((transfer, restore), (0, 0))


if __name__ == "__main__":
    unittest.main()
