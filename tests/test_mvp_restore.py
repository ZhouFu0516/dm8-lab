import json
import io
import tempfile
import unittest
import shlex
import subprocess
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path

from dm8_mvp.restore import build_script, load_config, main, parse_count_output, validate_config, validate_mapping_text
from dm8_mvp.runner import GuestResult, QgaRunner, QgaUnknown


class RestorePlanTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "guest": "dm8-restore-c",
            "backupset": "/data/restore-input/DM8RT01_FULL_20261124",
            "target": "/data/mvp/DM8MVP",
            "mapping": "/data/mvp/restore-paths-mvp.txt",
            "port": 15241,
        }

    def test_script_has_guards_and_stages(self):
        script = build_script(self.cfg)
        self.assertIn("test ! -e \"$TARGET\"", script)
        self.assertIn("CHECK BACKUPSET", script)
        self.assertIn("DUMP BACKUPSET", script)
        self.assertIn("mapping path escapes target", script)
        self.assertIn("RECOVER DATABASE", script)
        self.assertIn("UPDATE DB_MAGIC", script)
        self.assertIn("CONNECT /@LOCALHOST:$PORT AS SYSDBA", script)
        self.assertIn("WITH BACKUPDIR", script)
        self.assertIn("log directory already exists; refusing overwrite", script)
        self.assertIn("final instance name check", script)

    def test_example_config_is_valid(self):
        cfg = load_config(Path(__file__).parents[1] / "config/mvp-r1.example.json")
        self.assertEqual(cfg["guest"], "dm8-restore-c")
        self.assertEqual(cfg["port"], 15242)

    def test_protected_target_rejected(self):
        bad = dict(self.cfg, target="/data/restore-c/d1/new")
        with self.assertRaises(ValueError):
            validate_config(bad)

    def test_guest_and_path_boundaries_rejected(self):
        cases = [
            dict(self.cfg, guest="dm8-primary"),
            dict(self.cfg, target="/data/mvp"),
            dict(self.cfg, target="/data/mvp/../e7/unsafe"),
            dict(self.cfg, mapping="/data/e7/overwrite.txt"),
            dict(self.cfg, logdir="/data/restore-c/logs"),
            dict(self.cfg, db_name="UNRELATED"),
            dict(self.cfg, target="/data/mvp/DM8MVP;touch /tmp/pwn"),
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    validate_config(case)

    def test_dry_run_script_has_no_host_write_commands(self):
        script = build_script(self.cfg)
        self.assertNotIn("virsh start", script)
        self.assertNotIn("virsh attach", script)

    def test_count_output_is_observation_only(self):
        text = "\n".join(f"__COUNT_{name}__\n1          {n}" for name, n in zip(("LOCATIONS", "DEPARTMENTS", "JOBS", "EMPLOYEES", "JOB_HISTORY", "SALARIES"), (48, 160, 112, 600000, 1800000, 1599680)))
        self.assertEqual(parse_count_output(text)["SALARIES"], 1599680)

    def test_count_output_rejects_incomplete(self):
        with self.assertRaises(ValueError):
            parse_count_output("__COUNT_LOCATIONS__\n1 48\n")

    def test_count_output_requires_labels_and_rejects_duplicates(self):
        text = "\n".join(f"__COUNT_{name}__\n1 1" for name in ("LOCATIONS", "DEPARTMENTS", "JOBS", "EMPLOYEES", "JOB_HISTORY", "SALARIES"))
        with self.assertRaises(ValueError):
            parse_count_output(text + "\n__COUNT_LOCATIONS__\n1 2")

    def test_mapping_guard_rejects_quoted_unquoted_dotdot_and_mirror(self):
        good = 'data_path = "/data/mvp/DM8MVP/MAIN.DBF"'
        self.assertEqual(validate_mapping_text(good, self.cfg["target"]), ["/data/mvp/DM8MVP/MAIN.DBF"])
        for bad in (
            "data_path = /data/e7/MAIN.DBF",
            'data_path = "/data/mvp/DM8MVP/../../e7/MAIN.DBF"',
            'data_path = "/data/mvp/DM8MVP/MAIN.DBF"\nmirror_path = "/data/e7/MIRROR.DBF"',
            'data_path = "/data/mvp/DM8MVP/MAIN.DBF',
        ):
            with self.subTest(mapping=bad):
                with self.assertRaises(ValueError):
                    validate_mapping_text(bad, self.cfg["target"])
        script = build_script(self.cfg)
        self.assertIn("safe_mapping", script)
        self.assertIn("realpath -m", script)

    def test_generated_bash_mapping_guard_matches_python_on_path_fixtures(self):
        script=build_script(self.cfg)
        start=script.index("safe_mapping() {")
        guard=script[start:script.index("\nsafe_guest_path()",start)]
        cases=[
            ('data_path = "/data/mvp/DM8MVP/MAIN.DBF"',True),
            ("data_path = '/data/mvp/DM8MVP/MAIN.DBF'",True),
            ('data_path = /data/mvp/DM8MVP/MAIN.DBF',True),
            ('data_path = "/data/e7/MAIN.DBF"',False),
            ('data_path = "/data/mvp/DM8MVP/../../e7/MAIN.DBF"',False),
            ('data_path = "/data/mvp/DM8MVP_OTHER/MAIN.DBF"',False),
            ('data_path = "/data/mvp/DM8MVP/MAIN.DBF"\nmirror_path = "/data/e7/MIRROR.DBF"',False),
            ('data_path = "/data/mvp/DM8MVP/MAIN.DBF',False),
        ]
        with tempfile.TemporaryDirectory() as td:
            mapping=Path(td)/"mapping.txt"
            for content,expected in cases:
                with self.subTest(mapping=content):
                    mapping.write_text(content+"\n")
                    try:
                        validate_mapping_text(content,self.cfg["target"])
                        python_accepted=True
                    except ValueError:
                        python_accepted=False
                    header=("set -u\nfail() { echo \"FAIL:$*\"; exit 10; }\n"
                            f"TARGET={shlex.quote(self.cfg['target'])}\nMAPPING={shlex.quote(str(mapping))}\n")
                    result=subprocess.run(["bash","-c",header+guard+"\nsafe_mapping\n"],
                                          capture_output=True,text=True,timeout=5)
                    self.assertEqual(python_accepted,expected)
                    self.assertEqual(result.returncode==0,expected,result.stdout+result.stderr)


class QgaRunnerTests(unittest.TestCase):
    def test_nonzero_guest_exit_is_returned(self):
        runner = QgaRunner("x", timeout=1)
        responses = [
            {"return": {"pid": 7}},
            {"return": {"exited": True, "exitcode": 9, "out-data": "", "err-data": "ZXJy"}},
        ]
        with patch.object(runner, "_agent", side_effect=responses):
            result = runner.run("exit 9")
        self.assertEqual(result.exitcode, 9)
        self.assertEqual(result.stderr, "err")

    def test_timeout_is_unknown(self):
        runner = QgaRunner("x", timeout=0)
        with patch.object(runner, "_agent", side_effect=[{"return": {"pid": 8}}]):
            with self.assertRaises(QgaUnknown):
                runner.run("sleep 1")

    def test_missing_exitcode_is_unknown(self):
        runner = QgaRunner("x", timeout=1)
        responses = [{"return": {"pid": 9}}, {"return": {"exited": True}}]
        with patch.object(runner, "_agent", side_effect=responses):
            with self.assertRaises(QgaUnknown):
                runner.run("true")

    def test_agent_subprocess_timeout_is_unknown(self):
        runner = QgaRunner("x", agent_timeout=0.01)
        with patch("dm8_mvp.runner.subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("virsh", 0.01)):
            with self.assertRaises(QgaUnknown):
                runner._agent({"execute": "guest-exec-status"})


class ReportStatusTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"guest": "dm8-restore-c", "backupset": "/data/restore-input/E1", "target": "/data/mvp/DM8MVP", "mapping": "/data/mvp/map.txt", "port": 15242}

    def test_missing_marker_is_nonzero_and_not_pass(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"; path.write_text(json.dumps(dict(self.cfg, report_dir=td)))
            result = GuestResult(0, "other log", "", 1, True)
            with patch("dm8_mvp.restore.QgaRunner.run", return_value=result), redirect_stdout(io.StringIO()):
                rc = main(["--config", str(path), "--execute"])
            report = json.loads(next(p for p in Path(td).glob("*.json") if p.name != "config.json").read_text())
            self.assertNotEqual(rc, 0); self.assertEqual(report["status"], "FAIL")

    def test_marker_without_counts_is_incomplete_and_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"; path.write_text(json.dumps(dict(self.cfg, report_dir=td)))
            result = GuestResult(0, "MVP_RECOVERY_STAGES_PASS", "", 1, True)
            with patch("dm8_mvp.restore.QgaRunner.run", return_value=result), redirect_stdout(io.StringIO()):
                rc = main(["--config", str(path), "--execute"])
            report = json.loads(next(p for p in Path(td).glob("*.json") if p.name != "config.json").read_text())
            self.assertNotEqual(rc, 0); self.assertEqual(report["status"], "INCOMPLETE")

    def test_marker_and_counts_without_open_normal_is_not_pass(self):
        counts = "\n".join(f"__COUNT_{name}__\n1 1" for name in ("LOCATIONS", "DEPARTMENTS", "JOBS", "EMPLOYEES", "JOB_HISTORY", "SALARIES"))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"; path.write_text(json.dumps(dict(self.cfg, report_dir=td)))
            result = GuestResult(0, "MVP_RECOVERY_STAGES_PASS\n" + counts, "", 1, True)
            with patch("dm8_mvp.restore.QgaRunner.run", return_value=result), redirect_stdout(io.StringIO()):
                rc = main(["--config", str(path), "--execute"])
            report = json.loads(next(p for p in Path(td).glob("*.json") if p.name != "config.json").read_text())
            self.assertNotEqual(rc, 0); self.assertEqual(report["status"], "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()

