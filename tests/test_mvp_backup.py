import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dm8_mvp import backup


class B1BackupTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "primary_guest": "dm8-primary", "db_host": "192.168.241.11",
            "db_port": 5236, "db_service": "DM8RT01", "username": "SYSDBA",
            "backupset": "/data/dm8/DM8LAB/bak/mvp-b1",
            "logdir": "/data/dm8/DM8LAB/bak/mvp-b1-logs",
        }

    def test_preflight_requires_open_primary_and_archive(self):
        sql = backup.build_preflight_sql(self.cfg)
        self.assertIn("V$INSTANCE", sql)
        self.assertIn("__B1_ARCHIVE__", sql)
        good = "__B1_INSTANCE__\nDM8RT01|OPEN|PRIMARY\n__B1_ARCHIVE__\nY\n"
        self.assertEqual(backup.parse_preflight_output(good)["mode"], "PRIMARY")
        real = "SQL> __B1_INSTANCE__\nSQL> 1          DM8RT01|OPEN|PRIMARY\nSQL> __B1_ARCHIVE__\nSQL> 1          Y\n"
        self.assertEqual(backup.parse_preflight_output(real)["archive"],"Y")
        for bad in ("__B1_INSTANCE__\nDM8RT02|OPEN|PRIMARY\n__B1_ARCHIVE__\nY", "__B1_INSTANCE__\nDM8RT02|OPEN|STANDBY\n__B1_ARCHIVE__\nY", "__B1_INSTANCE__\nDM8RT01|MOUNT|PRIMARY\n__B1_ARCHIVE__\nY", "__B1_INSTANCE__\nDM8RT01|OPEN|PRIMARY\n__B1_ARCHIVE__\nN", "SQL> __B1_INSTANCE__\nSQL> 1 DM8RT01|OPEN|PRIMARY\nSQL> __B1_ARCHIVE__\nSQL> 1 N\n", "SQL> __B1_INSTANCE__\nSQL> 1 DM8RT01|OPEN|PRIMARY\nSQL> __B1_ARCHIVE__\nSQL> 1 Y\nSQL> 2 N\n"):
            with self.assertRaises(ValueError):
                backup.parse_preflight_output(bad)

    def test_backup_statement_is_online_full_physical(self):
        sql = backup.build_backup_sql(self.cfg)
        self.assertIn("BACKUP DATABASE FULL BACKUPSET '/data/dm8/DM8LAB/bak/mvp-b1';", sql)
        self.assertNotIn("dexp", sql.lower())

    def test_check_parser_requires_success(self):
        self.assertEqual(backup.parse_check_output("check backupset successfully\nB1_CHECK_PASS\n")["status"], "PASS")
        with self.assertRaises(ValueError):
            backup.parse_check_output("DM[-7169] failed\n")

    def test_fixed_source_and_path_validation(self):
        with self.assertRaises(ValueError):
            backup.validate_config(dict(self.cfg, primary_guest="dm8-standby1"))
        with self.assertRaises(ValueError):
            backup.validate_config(dict(self.cfg, backupset="/data/dm8/DM8LAB/../unsafe"))

    def test_dry_run_contains_all_stages(self):
        text = backup.build_dry_run(self.cfg)
        self.assertIn("__B1_INSTANCE__", text)
        self.assertIn("BACKUP DATABASE FULL BACKUPSET", text)
        self.assertIn("CHECK BACKUPSET", text)
        self.assertIn("no VM, QGA or database command is executed", text)

    def test_b1_output_can_feed_existing_r1_config(self):
        with tempfile.TemporaryDirectory() as td:
            restore = Path(td) / "r1.json"
            restore.write_text(json.dumps({"guest": "dm8-restore-c", "backupset": "/old", "target": "/data/mvp/R1", "mapping": "/data/mvp/map.txt", "db_name": "R1"}))
            cfg = dict(self.cfg, restore_config=str(restore))
            wired = backup.build_r1_input(cfg)
            self.assertEqual(wired["backupset"], self.cfg["backupset"])
            self.assertEqual(wired["guest"], "dm8-restore-c")

    def test_cli_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "b1.json"; cfg.write_text(json.dumps(self.cfg))
            result = subprocess.run(["python3", "-m", "dm8_mvp", "backup", "--config", str(cfg), "--dry-run"], capture_output=True, text=True, env={"PYTHONPATH": "src"})
            self.assertEqual(result.returncode, 0)
            self.assertIn("B1 dry-run", result.stdout)


if __name__ == "__main__":
    unittest.main()
