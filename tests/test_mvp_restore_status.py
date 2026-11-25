"""Offline regressions for the second real E2E's post-ALTER status output."""
import hashlib
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


FIXTURE = Path(__file__).parent / "fixtures/r1-second-e2e-verify.log"
COUNTS = dict(LOCATIONS=48, DEPARTMENTS=160, JOBS=112, EMPLOYEES=600000,
              JOB_HISTORY=1800000, SALARIES=1599680)


def bounded_real_output():
    # Preserve the historical fixture; add only the boundaries the new SQL emits.
    original = FIXTURE.read_text()
    row = "1          DM8RT01       OPEN    NORMAL"
    assert original.count(row) == 1
    return original.replace(row, "__R1_STATUS_BEGIN__\n" + row + "\n__R1_STATUS_END__")


class RestoreStatusTests(unittest.TestCase):
    def assert_contract(self, output, accepted):
        try:
            state = restore.parse_restore_status(output, "DM8RT01")
            counts = restore.parse_count_output(output)
            python_pass = True
        except ValueError:
            python_pass = False
        self.assertEqual(python_pass, accepted)
        # Run the actual generated Shell gate in a clean cwd without repo imports.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "verify.log").write_text(output)
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            script = (f"LOGDIR={shlex.quote(td)}\nINSTANCE_NAME=DM8RT01\n" +
                      restore.build_verification_gate())
            result = subprocess.run(["bash", "-c", script], cwd=td, env=env,
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0 if accepted else 10, result.stdout + result.stderr)
        if accepted:
            self.assertEqual(state, dict(instance="DM8RT01", status="OPEN", mode="NORMAL"))
            self.assertEqual(counts, COUNTS)

    def test_real_mount_login_then_open_normal_query(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                         '4964d9d577602b15789e02dd1520324c628ba022c21e2272240b438d5dd299a3')
        self.assert_contract(bounded_real_output(), True)

    def test_open_login_cannot_override_mount_query(self):
        output = bounded_real_output().replace("mode is primary, state is mount",
                                              "mode is normal, state is open")
        self.assert_contract(output.replace("DM8RT01       OPEN    NORMAL",
                                            "DM8RT01       MOUNT   NORMAL"), False)

    def test_normal_login_cannot_override_sql_error(self):
        self.assert_contract(bounded_real_output().replace("mode is primary, state is mount",
                             "mode is normal, state is open") + "\n[-2007]: Syntax error\n", False)

    def test_missing_boundaries_or_empty_query_rejected(self):
        raw = FIXTURE.read_text()
        self.assert_contract(raw, False)
        for old in ("1          DM8RT01       OPEN    NORMAL", "__R1_STATUS_BEGIN__", "__R1_STATUS_END__"):
            with self.subTest(missing=old):
                self.assert_contract(bounded_real_output().replace(old, ""), False)

    def test_ambiguous_duplicate_or_unbounded_results_rejected(self):
        row = "1          DM8RT01       OPEN    NORMAL"
        for extra in (row, "2          DM8RT01       OPEN    NORMAL", "unexpected result"):
            with self.subTest(extra=extra):
                self.assert_contract(bounded_real_output().replace(row, row + "\n" + extra), False)
        self.assert_contract(bounded_real_output() + "\n__R1_STATUS_BEGIN__\n", False)
        self.assert_contract(bounded_real_output().replace("__R1_STATUS_BEGIN__", "__R1_STATUS_END__", 1), False)

    def test_wrong_instance_field_order_or_state_rejected(self):
        for fields in ("DM8RT01 OPEN SUSPEND", "DM8RT01 SUSPEND NORMAL",
                       "DM8RT01 NORMAL OPEN", "OTHER OPEN NORMAL", "DM8RT01 OPEN NORMAL trailing"):
            with self.subTest(fields=fields):
                self.assert_contract(bounded_real_output().replace("DM8RT01       OPEN    NORMAL", fields), False)

    def test_sql_errors_anywhere_rejected(self):
        for error in ("DISQL-2007", "DM[-2007]", "[-2007]", "Error in line: 1", "Invalid table"):
            with self.subTest(error=error):
                self.assert_contract(bounded_real_output() + "\n" + error, False)

    def test_crlf_ansi_prompts_and_whitespace(self):
        output = bounded_real_output().replace("DM8RT01       OPEN    NORMAL",
                                              "\x1b[32mDM8RT01\tOPEN\tNORMAL\x1b[0m")
        self.assert_contract("\x1b]0; DIsql\x07" + output.replace("\n", "\r\n"), True)

    def test_normal_state_with_missing_invalid_or_duplicate_count_rejected(self):
        for old, new in (("__COUNT_JOBS__", "missing marker"),
                         ("1          112", "1          invalid"),
                         ("1          112", "1          -1"),
                         ("1          112", "1          112\n2          112"),
                         ("__COUNT_JOBS__", "__COUNT_JOBS__\n__COUNT_JOBS__")):
            with self.subTest(new=new):
                self.assert_contract(bounded_real_output().replace(old, new), False)

    def test_r1_main_accepts_bounded_real_state_and_counts(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = dict(guest="dm8-restore-c", backupset="/data/mvp60/i1-input/test",
                       target="/data/mvp60/TEST", mapping="/data/mvp60/test-map.txt",
                       instance_name="DM8RT01", report_dir=td, run_id="offline-r1")
            path = Path(td) / "config.json"
            path.write_text(json.dumps(cfg))
            output = ("MVP_PHYSICAL_RECOVERY_PASS\nSTAGE START-VERIFY\n" +
                      bounded_real_output() + "\nMVP_RECOVERY_STAGES_PASS\n")
            with patch("dm8_mvp.restore.QgaRunner.run", return_value=GuestResult(0, output, "", 7, True)), redirect_stdout(io.StringIO()):
                rc = restore.main(["--config", str(path), "--execute"])
            report = json.loads((Path(td) / "offline-r1.json").read_text())
            self.assertEqual(rc, 0)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["counts"], COUNTS)
            self.assertEqual(report["stages"], dict(physical_recovery="PASS", verification="PASS"))

    def test_r1_main_completion_marker_cannot_override_bad_query(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = dict(guest="dm8-restore-c", backupset="/data/mvp60/i1-input/test",
                       target="/data/mvp60/TEST", mapping="/data/mvp60/test-map.txt",
                       instance_name="DM8RT01", report_dir=td, run_id="offline-r1")
            path = Path(td) / "config.json"
            path.write_text(json.dumps(cfg))
            output = ("MVP_PHYSICAL_RECOVERY_PASS\nSTAGE START-VERIFY\n" +
                      bounded_real_output().replace("DM8RT01       OPEN    NORMAL", "DM8RT01 MOUNT NORMAL"))
            with patch("dm8_mvp.restore.QgaRunner.run", return_value=GuestResult(0, output + "\nMVP_RECOVERY_STAGES_PASS\n", "", 7, True)), redirect_stdout(io.StringIO()):
                rc = restore.main(["--config", str(path), "--execute"])
            report = json.loads((Path(td) / "offline-r1.json").read_text())
            self.assertNotEqual(rc, 0)
            self.assertNotEqual(report["status"], "PASS")
            self.assertIsNone(report["counts"])
