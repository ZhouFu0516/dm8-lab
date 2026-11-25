import json
import base64
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from dm8_mvp.integration import build_i1_dry_run, build_primary_script, build_transfer_plan, parse_hashes, parse_manifest, validate_config, main
from dm8_mvp.runner import GuestResult


class I1IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "primary_guest": "dm8-primary", "db_host": "192.168.241.11", "db_port": 5236,
            "db_service": "DM8RT01", "username": "SYSDBA",
            "credential_file": "/etc/dm8-lab/sysdba",
            "backupset": "/data/dm8/DM8LAB/bak/mvp-b1", "logdir": "/data/dm8/DM8LAB/bak/mvp-b1-logs",
            "backup_root":"/data/dm8/DM8LAB/bak/i1","primary_log_root":"/data/dm8/DM8LAB/bak/i1-logs",
            "primary_mount": "/data","primary_device":"/dev/vdb","primary_uuid":"28b77482-9a5b-4771-9b75-779e2c002e17",
            "primary_log_root":"/data/dm8/DM8LAB/bak/i1-logs","primary_required_bytes":200,
            "restore_mount":"/data/mvp60","restore_device":"/dev/vdf","restore_uuid":"56e230a8-1b70-4cee-bb1c-4f8cd68387cf",
            "restore_input_root":"/data/mvp60/i1-input","restore_target_root":"/data/mvp60","restore_required_bytes":300,
            "historical_restore_bytes":100,"backup_estimate_bytes":50,"logs_reserve_bytes":20,"safety_margin_percent":25,
            "restore_guest": "dm8-restore-c", "restore_input": "/data/mvp60/i1-input/PLACEHOLDER", "report_dir": "outputs/i1",
            "tables_config":"config/tables.json","restore_config":"config/mvp-r1.example.json"
        }

    def test_primary_script_reads_credential_and_checks_instance(self):
        text = build_primary_script(self.cfg)
        match=re.search(r'b64decode\(\"([A-Za-z0-9+/=]+)\"\)',text)
        self.assertIsNotNone(match)
        decoded=base64.b64decode(match.group(1)).decode()
        self.assertIn("guest_runtime",decoded)
        self.assertIn("/etc/dm8-lab/sysdba",decoded)
        self.assertNotIn("password=",decoded.lower())

    def test_transfer_plan_uses_qga_file_method(self):
        plan = build_transfer_plan(self.cfg)
        self.assertIn("guest-file-read", plan)
        self.assertIn("guest-file-write", plan)
        self.assertIn("dm8-restore-c", plan)

    def test_manifest_parser_requires_backup_files(self):
        self.assertEqual(parse_manifest("DM8RT01_FULL.bak 123\nDM8RT01_FULL.meta 44\n"), [("DM8RT01_FULL.bak", 123), ("DM8RT01_FULL.meta", 44)])
        with self.assertRaises(ValueError):
            parse_manifest("README.txt 1\n")

    def test_hash_manifest_parser(self):
        digest = "a" * 64
        self.assertEqual(parse_hashes(f"{digest}  /data/bak/a.bak\n")["a.bak"], digest)
        with self.assertRaises(ValueError):
            parse_hashes("bad hash\n")

    def test_fixed_restore_target_and_dry_run(self):
        validate_config(self.cfg)
        text = build_i1_dry_run(self.cfg)
        self.assertIn("I1 dry-run", text)
        self.assertIn("PRIMARY execution request (not sent)", text)
        self.assertIn("BACKUP DATABASE FULL BACKUPSET", text)
        self.assertIn("R1 restore config", text)
        with self.assertRaises(ValueError):
            validate_config(dict(self.cfg, restore_guest="dm8-primary"))

    def test_run_paths_are_unique_and_not_r5(self):
        from dm8_mvp.integration import prepare_run_config
        run = prepare_run_config(self.cfg, "i1-test-123")
        self.assertIn("i1-test-123", run["backupset"])
        self.assertIn("i1-test-123", run["restore_input"])
        self.assertNotIn("DM8RT01_R5", run["restore_target"])
        self.assertEqual(run["db_name"],Path(run["restore_target"]).name)
        self.assertRegex(run["db_name"],r"^[A-Za-z][A-Za-z0-9_]*$")
        self.assertNotIn(".",run["db_name"])

    def test_cli_run_defaults_to_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "i1.json"; p.write_text(json.dumps(self.cfg))
            result = subprocess.run(["python3", "-m", "dm8_mvp", "run", "--config", str(p)], env={"PYTHONPATH": "src"}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertIn("I1 dry-run", result.stdout)

    def test_execute_normal_path_mock_reaches_restore_and_comparison(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = dict(self.cfg, report_dir=td)
            path = Path(td) / "i1.json"; path.write_text(json.dumps(cfg))
            digest = "a" * 64
            stdout=json.dumps({"status":"PASS","manifest":"a.bak 3\na.meta 2","hashes":f"{digest}  /bak/a.bak\n{digest}  /bak/a.meta"})
            counts={name:1 for name in ("LOCATIONS","DEPARTMENTS","JOBS","EMPLOYEES","JOB_HISTORY","SALARIES")}
            baseline={"source_type":"DATABASE","schema_version":"1.0","fingerprint_version":"1.0","tables":{},"total_row_count":6}
            def fake_restore(run_cfg):
                report_dir=Path(run_cfg["r1_report_dir"]);report_dir.mkdir(parents=True)
                (report_dir/f"{run_cfg['run_id']}.json").write_text(json.dumps({"run_id":run_cfg["run_id"],"status":"PASS","verification":"ROW_COUNT_OBSERVED","comparison":"NOT EXECUTED","counts":counts}))
                return 0
            with patch("dm8_mvp.integration.invoke_guest",return_value={"status":"PASS","available_bytes":9999}),patch("dm8_mvp.integration.collect_source_baseline",return_value=baseline),patch("dm8_mvp.integration.compare_row_count_baselines",return_value={"status":"PASS"}),patch("dm8_mvp.integration.execute_primary",return_value=GuestResult(0,stdout,"",1,True)),patch("dm8_mvp.integration.prepare_restore_input",return_value={"status":"PASS"}),patch("dm8_mvp.integration.transfer_manifest",return_value=[{"name":"a.bak"},{"name":"a.meta"}]),patch("dm8_mvp.integration.verify_target_hashes",return_value={"a.bak":digest,"a.meta":digest}),patch("dm8_mvp.integration.execute_r1",side_effect=fake_restore):
                rc = main(["--config", str(path), "--execute"])
            self.assertEqual(rc, 0)
            report = json.loads(next(Path(td).glob("*-i1.json")).read_text())
            self.assertEqual(report["status"], "PASS")

    def test_execute_primary_failure_stops_before_transfer(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = dict(self.cfg, report_dir=td)
            path = Path(td) / "i1.json"; path.write_text(json.dumps(cfg))
            with patch("dm8_mvp.integration.invoke_guest",return_value={"status":"PASS","available_bytes":9999}),patch("dm8_mvp.integration.collect_source_baseline",return_value={"source_type":"DATABASE"}),patch("dm8_mvp.integration.execute_primary", return_value=GuestResult(0, json.dumps({"status":"FAIL","reason":"mock"}), "", 1, True)), patch("dm8_mvp.integration.transfer_manifest") as transfer:
                rc = main(["--config", str(path), "--execute"])
            self.assertNotEqual(rc, 0)
            transfer.assert_not_called()

    def test_source_instability_stops_before_transfer(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = dict(self.cfg, report_dir=td)
            path = Path(td) / "i1.json"; path.write_text(json.dumps(cfg))
            with patch("dm8_mvp.integration.invoke_guest",return_value={"status":"PASS","available_bytes":9999}),patch("dm8_mvp.integration.collect_source_baseline", side_effect=[{"source_type":"DATABASE"},{"source_type":"DATABASE"}]), patch("dm8_mvp.integration.compare_row_count_baselines", return_value={"status": "FAIL"}), patch("dm8_mvp.integration.execute_primary", return_value=GuestResult(0, json.dumps({"status":"PASS","manifest":"a.bak 1\na.meta 1","hashes":""}), "", 1, True)), patch("dm8_mvp.integration.transfer_manifest") as transfer:
                rc = main(["--config", str(path), "--execute"])
            self.assertNotEqual(rc, 0)
            transfer.assert_not_called()

    def test_r1_execute_switch_is_passed(self):
        from dm8_mvp.integration import execute_r1
        with tempfile.TemporaryDirectory() as td:
            restore = Path(td) / "r1.json"
            restore.write_text(json.dumps({"guest": "dm8-restore-c", "backupset": "/old", "target": "/data/mvp/R1", "mapping": "/data/mvp/map.txt", "logdir": "/data/mvp/log", "db_name": "R1"}))
            cfg = dict(self.cfg, restore_config=str(restore), restore_target="/data/mvp/R1", db_name="R1",mapping="/data/mvp/map.txt", restore_logdir="/data/mvp/log", r1_report_dir=td,run_id="i1-x")
            with patch("dm8_mvp.integration.restore.main", return_value=0) as call:
                execute_r1(cfg)
            self.assertIn("--execute", call.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
