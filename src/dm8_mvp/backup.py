"""Minimal B1 physical-backup orchestration primitives.

This module deliberately supports the fixed research environment only.  It
constructs and validates the normal path; live execution remains opt-in and is
not used by the offline tests.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from dm8_verify.collectors.disql_results import parse_primary_preflight

def _b1_path(value: str, label: str) -> Path:
    """Validate a fixed guest path without applying restore-target protections."""
    if not isinstance(value, str) or not value.startswith("/") or any(part in (".", "..") for part in value.split("/")):
        raise ValueError(f"{label} must be an absolute normalized path")
    if any(ord(ch) < 32 or ch in "'\";|&$`\n\r" for ch in value):
        raise ValueError(f"{label} contains unsafe characters")
    return Path(value)


def validate_config(cfg: dict[str, Any]) -> None:
    required = ("primary_guest", "backupset", "logdir", "db_host", "db_port", "db_service", "username")
    missing = [key for key in required if not cfg.get(key)]
    if missing:
        raise ValueError(f"missing B1 config keys: {', '.join(missing)}")
    if cfg["primary_guest"] != "dm8-primary":
        raise ValueError("B1 backup source must be the fixed dm8-primary guest")
    _b1_path(cfg["backupset"], "backupset")
    _b1_path(cfg["logdir"], "logdir")
    if not 1 <= int(cfg["db_port"]) <= 65535:
        raise ValueError("invalid database port")
    for key in ("db_host", "db_service", "username"):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", str(cfg[key])):
            raise ValueError(f"invalid {key}")


def build_preflight_sql(cfg: dict[str, Any]) -> str:
    """Build read-only PRIMARY/OPEN/archive preflight SQL."""
    validate_config(cfg)
    user = cfg["username"]
    host, port, service = cfg["db_host"], int(cfg["db_port"]), cfg["db_service"]
    archive_sql = cfg.get("archive_sql", "SELECT ARCH_MODE FROM V$DATABASE;")
    return "\n".join([
        "SET HEADING OFF", "SET FEEDBACK OFF", "SET PAGESIZE 0",
        f"CONNECT {user}/$DM8_BACKUP_PASSWORD@{host}:{port}",
        "PROMPT __B1_INSTANCE__",
        "SELECT INSTANCE_NAME || '|' || STATUS$ || '|' || MODE$ FROM V$INSTANCE;",
        "PROMPT __B1_ARCHIVE__", archive_sql,
        "EXIT SUCCESS", "",
    ])


def parse_preflight_output(output: str, expected_instance: str = "DM8RT01") -> dict[str, str]:
    """Parse explicit preflight markers and require an OPEN PRIMARY instance."""
    return parse_primary_preflight(output, expected_instance)


def build_backup_sql(cfg: dict[str, Any]) -> str:
    """Build the official DM8 online full physical backup statement."""
    validate_config(cfg)
    backupset = str(_b1_path(cfg["backupset"], "backupset"))
    return "\n".join([
        "SET ECHO OFF", "SET FEEDBACK OFF",
        f"CONNECT {cfg['username']}/$DM8_BACKUP_PASSWORD@{cfg['db_host']}:{int(cfg['db_port'])}",
        f"BACKUP DATABASE FULL BACKUPSET '{backupset}';",
        "EXIT SUCCESS", "",
    ])


def build_check_script(cfg: dict[str, Any]) -> str:
    """Build the DMRMAN CHECK command used after backup creation."""
    validate_config(cfg)
    backupset = shlex.quote(str(_b1_path(cfg["backupset"], "backupset")))
    logdir = shlex.quote(str(_b1_path(cfg["logdir"], "logdir")))
    return f'''set -u
BACKUPSET={shlex.quote(str(_b1_path(cfg["backupset"], "backupset")))}
LOGDIR={logdir}
test ! -e "$LOGDIR" || {{ echo "FAIL:log directory exists"; exit 10; }}
mkdir -p "$LOGDIR"
test -d "$BACKUPSET" || {{ echo "FAIL:backupset missing"; exit 10; }}
runuser -u dmdba -- /opt/dmdbms/bin/dmrman USE_AP=2 CTLSTMT="CHECK BACKUPSET '$BACKUPSET' LEVEL 1" >"$LOGDIR/check.log" 2>&1 || exit 10
grep -Eqi 'check backupset successfully' "$LOGDIR/check.log" || exit 10
echo B1_CHECK_PASS
'''


def parse_check_output(output: str) -> dict[str, str]:
    if "B1_CHECK_PASS" not in output:
        raise ValueError("CHECK BACKUPSET did not produce success marker")
    if re.search(r"DM\[-\d+\]|error|failed", output, re.I):
        raise ValueError("CHECK BACKUPSET output contains an error")
    return {"status": "PASS", "evidence": "CHECK BACKUPSET"}


def build_dry_run(cfg: dict[str, Any]) -> str:
    validate_config(cfg)
    return "\n".join([
        "# B1 dry-run: no VM, QGA or database command is executed",
        "# PRECHECK SQL:", build_preflight_sql(cfg),
        "# BACKUP SQL:", build_backup_sql(cfg),
        "# CHECK SCRIPT:", build_check_script(cfg),
    ])


def build_r1_input(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return the existing R1 config with this B1 backupset wired in."""
    restore_config = cfg.get("restore_config")
    if not restore_config:
        raise ValueError("restore_config is required to wire B1 output to R1")
    path = Path(restore_config)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["backupset"] = cfg["backupset"]
    data["guest"] = cfg.get("restore_guest", "dm8-restore-c")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dm8_mvp backup")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    validate_config(cfg)
    if args.dry_run:
        print(build_dry_run(cfg), end="")
        return 0
    raise RuntimeError("live B1 execution is not enabled; use --dry-run until separately authorized")


if __name__ == "__main__":
    raise SystemExit(main())
