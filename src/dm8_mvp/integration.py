"""I1 minimal B1 -> transfer -> R1 integration plan.

The default command is a dry-run. The execute path is intentionally explicit
and uses the already-tested QGA runner; it is not invoked by tests.
"""
from __future__ import annotations

import argparse
import inspect
import json
import shlex
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid
from . import guest_runtime
from dm8_verify.collectors import disql_results
from dm8_verify.collectors.dm8 import count_baseline_from_output
from dm8_verify.collectors.disql_results import parse_primary_preflight, parse_instance_status

from .backup import validate_config as validate_b1
from . import restore
from .runner import QgaRunner, QgaUnknown


class I1StageError(RuntimeError):
    def __init__(self, status: str, message: str, guest_result: dict | None = None):
        super().__init__(message)
        self.status = status
        self.guest_result = guest_result


def validate_config(cfg: dict[str, Any]) -> None:
    validate_b1(cfg)
    if cfg.get("restore_guest", "dm8-restore-c") != "dm8-restore-c":
        raise ValueError("I1 restore guest must be dm8-restore-c")
    for key in ("restore_input", "report_dir", "credential_file", "backup_root", "restore_input_root", "restore_target_root"):
        if not cfg.get(key):
            raise ValueError(f"missing I1 config key: {key}")
    if not str(cfg["restore_input"]).startswith("/"):
        raise ValueError("restore_input must be an absolute guest path")
    if cfg.get("credential_file") != "/etc/dm8-lab/sysdba":
        raise ValueError("fixed PRIMARY credential path must be /etc/dm8-lab/sysdba")
    if cfg.get("restore_target_root") != "/data/mvp60" or cfg.get("restore_input_root") != "/data/mvp60/i1-input":
        raise ValueError("fixed I1 data paths must use /data/mvp60")
    if cfg.get("restore_mount") != "/data/mvp60" or cfg.get("restore_device") != "/dev/vdf":
        raise ValueError("restore volume must be the approved /dev/vdf on /data/mvp60")
    if not re.fullmatch(r"[0-9a-f-]{36}", str(cfg.get("restore_uuid", ""))):
        raise ValueError("restore filesystem UUID is required")
    if cfg.get("backup_root") != "/data/dm8/DM8LAB/bak/i1" or cfg.get("primary_log_root") != "/data/dm8/DM8LAB/bak/i1-logs":
        raise ValueError("fixed PRIMARY backup/log paths differ from approved isolated roots")
    if not (0 < int(cfg.get("safety_margin_percent", 0)) <= 50):
        raise ValueError("safety margin must be explicitly configured")


def prepare_run_config(cfg: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Create per-run guest paths; never reuse the historical R5 paths."""
    validate_config(cfg)
    run = dict(cfg)
    run["run_id"] = run_id
    dm8_token = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
    run["dm8_token"] = dm8_token
    run["backupset"] = str(Path(cfg["backup_root"]) / run_id)
    run["restore_input"] = str(Path(cfg["restore_input_root"]) / run_id)
    db_name = f"DM8I1_{dm8_token}"
    if len(db_name) > 30 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", db_name):
        raise ValueError("generated DM8 database name is invalid")
    target = Path(cfg["restore_target_root"]) / db_name
    run["db_name"] = db_name
    run["restore_target"] = str(target)
    run["mapping"] = str(target.parent / f"restore-paths-{run_id}.txt")
    run["restore_logdir"] = str(target.parent / f"mvp-logs-{run_id}")
    run["logdir"] = str(Path(cfg["primary_log_root"]) / run_id)
    run["r1_report_dir"] = str(Path(cfg["report_dir"]) / run_id / "r1")
    run["i1_report_path"] = str(Path(cfg["report_dir"]) / f"{run_id}-i1.json")
    return run


def build_primary_script(cfg: dict[str, Any]) -> str:
    """Build a guest script with runtime credential-file substitution."""
    validate_config(cfg)
    cred = shlex.quote(str(cfg["credential_file"]))
    logdir = shlex.quote(str(cfg["logdir"]))
    backup = shlex.quote(str(cfg["backupset"]))
    user = shlex.quote(str(cfg["username"]))
    host = shlex.quote(str(cfg["db_host"]))
    port = int(cfg["db_port"])
    return fr"""set -u
set -o pipefail
umask 077
CRED={cred}; LOGDIR={logdir}; BACKUPSET={backup}; USERNAME={user}; DBHOST={host}; DBPORT={port}
fail() {{ echo "I1_FAIL:$*"; exit 10; }}
test -r "$CRED" || fail credential_file_missing
test ! -e "$LOGDIR" || fail logdir_exists
mkdir -p "$LOGDIR" || fail logdir_create
PW=$(sed -n -E 's/^[[:space:]]*password[[:space:]]*=[[:space:]]*//p' "$CRED" | head -n1)
[ -n "$PW" ] || PW=$(sed -n '/^[[:space:]]*#/d;/^[[:space:]]*$/d;q' "$CRED")
[ -n "$PW" ] || fail credential_missing
{{
  echo 'SET HEADING OFF'; echo 'SET FEEDBACK OFF'; echo 'SET PAGESIZE 0'
  printf 'CONNECT %s/%s@%s:%s\n' "$USERNAME" "$PW" "$DBHOST" "$DBPORT"
  echo 'PROMPT __B1_INSTANCE__'
  printf '%s\\n' "SELECT INSTANCE_NAME || '|' || STATUS\\$ || '|' || MODE\\$ FROM V\\$INSTANCE;"
  echo 'PROMPT __B1_ARCHIVE__'; echo 'SELECT ARCH_MODE FROM V$DATABASE;'
  echo 'EXIT SUCCESS'
}} | runuser -u dmdba -- /opt/dmdbms/bin/disql /nolog >"$LOGDIR/preflight.log" 2>&1 || fail preflight
grep -Eq 'DM8RT01[|][[:space:]]*OPEN[|][[:space:]]*PRIMARY' "$LOGDIR/preflight.log" || fail source_not_open_primary
awk '/__B1_ARCHIVE__/{{getline; print}}' "$LOGDIR/preflight.log" | grep -Eq '^[[:space:]]*(Y|YES|1|ARCHIVE)[[:space:]]*$' || fail archive_not_enabled
echo I1_STAGE_PREFLIGHT_PASS
{{
  echo 'SET ECHO OFF'; echo 'SET FEEDBACK OFF'
  printf 'CONNECT %s/%s@%s:%s\n' "$USERNAME" "$PW" "$DBHOST" "$DBPORT"
  echo "BACKUP DATABASE FULL BACKUPSET '$BACKUPSET';"
  echo 'EXIT SUCCESS'
}} | runuser -u dmdba -- /opt/dmdbms/bin/disql /nolog >"$LOGDIR/backup.log" 2>&1 || fail backup
grep -Eqi 'executed successfully|backup[[:space:]].*success' "$LOGDIR/backup.log" || fail backup_not_confirmed
echo I1_STAGE_BACKUP_PASS
runuser -u dmdba -- /opt/dmdbms/bin/dmrman USE_AP=2 CTLSTMT="CHECK BACKUPSET '$BACKUPSET' LEVEL 1" >"$LOGDIR/check.log" 2>&1 || fail check
grep -Eqi 'check backupset successfully' "$LOGDIR/check.log" || fail check_not_confirmed
echo I1_STAGE_CHECK_PASS
find "$BACKUPSET" -maxdepth 1 -type f \( -name '*.bak' -o -name '*.meta' \) -printf '%f %s\n' | sort >"$LOGDIR/manifest.txt" || fail manifest
test -s "$LOGDIR/manifest.txt" || fail manifest_empty
sha256sum "$BACKUPSET"/*.bak "$BACKUPSET"/*.meta | tee "$LOGDIR/sha256.txt" || fail sha256
cat "$LOGDIR/manifest.txt"
echo I1_PRIMARY_PASS
"""


def preflight_queries() -> list[str]:
    return ["PROMPT __B1_INSTANCE__",
            "SELECT INSTANCE_NAME || '|' || STATUS$ || '|' || MODE$ FROM V$INSTANCE;",
            "PROMPT __B1_ARCHIVE__", "SELECT ARCH_MODE FROM V$DATABASE;"]


def six_count_queries() -> list[str]:
    names = ("LOCATIONS", "DEPARTMENTS", "JOBS", "EMPLOYEES", "JOB_HISTORY", "SALARIES")
    return [line for name in names for line in (
        f"PROMPT __DM8_VERIFY_COUNT_BEGIN__{name}",
        f"SELECT COUNT(*) FROM HR_TEST.{name};",
        f"PROMPT __DM8_VERIFY_COUNT_END__{name}") ]


def build_guest_request(payload: dict[str, Any], label: str) -> str:
    """Send the fixed runtime and parser sources; require no installed guest packages."""
    import base64
    script = "\n".join([
        "import json, sys, types",
        "for name in ('dm8_verify', 'dm8_verify.collectors'):",
        "    package = types.ModuleType(name)",
        "    package.__path__ = []",
        "    sys.modules[name] = package",
        "parser = types.ModuleType('dm8_verify.collectors.disql_results')",
        "sys.modules[parser.__name__] = parser",
        f"exec(compile({inspect.getsource(disql_results)!r}, 'disql_results.py', 'exec'), parser.__dict__)",
        "runtime = {'__name__': 'dm8_mvp.guest_runtime'}",
        f"exec(compile({inspect.getsource(guest_runtime)!r}, 'guest_runtime.py', 'exec'), runtime)",
        f"runtime['guest_main'](json.loads({json.dumps(payload)!r}))",
    ])
    encoded = base64.b64encode(script.encode()).decode("ascii")
    return f"python3 -c 'import base64;exec(base64.b64decode(\"{encoded}\"))'"


def build_primary_script(cfg: dict[str, Any]) -> str:
    validate_config(cfg)
    return build_guest_request(primary_payload(cfg), "primary-backup-check")


def primary_payload(cfg: dict[str, Any]) -> dict[str, Any]:
    return dict(operation="primary", credential_file=cfg["credential_file"], username=cfg["username"],
        db_host=cfg["db_host"], db_port=int(cfg["db_port"]), backupset=cfg["backupset"], logdir=cfg["logdir"],
        preflight_queries=preflight_queries(), required_bytes=cfg["primary_required_bytes"], mount=cfg["primary_mount"],
        device=cfg["primary_device"], uuid=cfg["primary_uuid"], new_paths=[cfg["backupset"],cfg["logdir"]],
        check_dmdba_write=True, sql_timeout=cfg.get("sql_timeout",900))


def build_transfer_plan(cfg: dict[str, Any]) -> str:
    validate_config(cfg)
    return "\n".join([
        "# E4 method: guest-file-read -> guest-file-write via QGA",
        f"source_guest={cfg['primary_guest']}",
        f"source_dir={cfg['backupset']}",
        f"target_guest={cfg.get('restore_guest', 'dm8-restore-c')}",
        f"target_dir={cfg['restore_input']}",
        "# Copy manifest-listed .bak/.meta files, then compare SHA-256 before R1.",
    ]) + "\n"


def build_i1_dry_run(cfg: dict[str, Any]) -> str:
    validate_config(cfg)
    run_cfg = prepare_run_config(cfg, "i1_DRYRUN0001")
    run_cfg["primary_required_bytes"]=required_primary_bytes(run_cfg)
    run_cfg["restore_required_bytes"]=required_restore_bytes(run_cfg)
    return "\n".join([
        "# I1 dry-run: no VM, QGA, mkdir, BACKUP, transfer or RESTORE is executed",
        "# PRIMARY readonly probe:", json.dumps(primary_probe_payload(run_cfg), ensure_ascii=False, indent=2),
        "# restore-c readonly probe:", json.dumps(restore_probe_payload(run_cfg), ensure_ascii=False, indent=2),
        "# source COUNT query markers:", *six_count_queries(),
        "# SQL statements executed in the guest after gates:",
        f"BACKUP DATABASE FULL BACKUPSET '{run_cfg['backupset']}';",
        f"dmrman USE_AP=2 CTLSTMT=CHECK BACKUPSET '{run_cfg['backupset']}' LEVEL 1",
        "# PRIMARY execution request (not sent):", build_primary_script(run_cfg),
        "# TRANSFER PLAN:", build_transfer_plan(run_cfg),
        "# R1 restore config (not executed):", json.dumps(derived_restore_config(run_cfg), ensure_ascii=False, indent=2),
    ])


def execute_primary(cfg: dict[str, Any]):
    """Explicit live entry point; callers must pass --execute."""
    return QgaRunner(cfg["primary_guest"], cfg.get("timeout", 900)).run(build_primary_script(cfg))


def required_restore_bytes(cfg: dict[str, Any]) -> int:
    subtotal = int(cfg["historical_restore_bytes"]) + int(cfg["backup_estimate_bytes"]) + int(cfg["logs_reserve_bytes"])
    return (subtotal * (100 + int(cfg["safety_margin_percent"])) + 99) // 100


def required_primary_bytes(cfg: dict[str, Any]) -> int:
    estimate = int(cfg["backup_estimate_bytes"])
    return (estimate * (100 + int(cfg["safety_margin_percent"])) + 99) // 100


def primary_probe_payload(cfg: dict[str, Any]) -> dict[str, Any]:
    return dict(operation="probe", mount=cfg["primary_mount"], device=cfg["primary_device"],
        uuid=cfg["primary_uuid"], required_bytes=required_primary_bytes(cfg),
        new_paths=[cfg["backupset"],cfg["logdir"]], check_dmdba_write=True,
        protected=["/data/dm8/DM8LAB/bak/e1-primary-20261124"])


def restore_probe_payload(cfg: dict[str, Any]) -> dict[str, Any]:
    return dict(operation="probe", mount=cfg["restore_mount"], device=cfg["restore_device"],
        uuid=cfg["restore_uuid"], required_bytes=required_restore_bytes(cfg),
        new_paths=[cfg["restore_input"],cfg["restore_target"],cfg["mapping"],cfg["restore_logdir"]],
        check_dmdba_write=True, port=int(cfg.get("port",15242)),
        protected=["/data/mvp60/DM8RT01_R5","/data/mvp60/DM8RT01_R4","/data/mvp40/DM8RT01_R3","/data/e7","/data/restore-c"])


def invoke_guest(domain: str, payload: dict[str, Any], timeout: int = 900):
    result = QgaRunner(domain, timeout).run(build_guest_request(payload, payload.get("operation","guest-op")))
    if not result.exited or result.exitcode is None:
        raise QgaUnknown(f"{domain} guest operation has no final exit code; no retry")
    try:
        value=json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{domain} guest result was not JSON (guest exitcode={result.exitcode})") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{domain} guest result was not an object")
    if value.get("unknown"):
        raise QgaUnknown(f"{domain}: {value.get('reason', 'guest command outcome unknown')}; no retry")
    if value.get("status") != "PASS" or result.exitcode != 0:
        status = "BLOCKED" if value.get("status") == "BLOCKED" else "FAIL"
        raise I1StageError(status, f"{domain}: {value.get('reason', value.get('status'))} "
                           f"(guest exitcode={result.exitcode}, tool exitcode={value.get('exitcode')})", value)
    if payload.get("operation") == "sql" and value.get("exitcode") != 0:
        raise I1StageError("FAIL", f"{domain} SQL result has no successful tool exit code", value)
    return value


def parse_manifest(output: str) -> list[tuple[str, int]]:
    files = []
    for line in output.splitlines():
        match = re.fullmatch(r"([^/\s]+\.(?:bak|meta))\s+(\d+)", line.strip(), re.I)
        if match:
            files.append((match.group(1), int(match.group(2))))
    if not files:
        raise ValueError("primary output contains no backup manifest files")
    return files


def parse_hashes(output: str) -> dict[str, str]:
    hashes = {}
    for line in output.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})  (?:.*/)?([^/\s]+\.(?:bak|meta))", line.strip(), re.I)
        if match:
            hashes[match.group(2)] = match.group(1).lower()
    if not hashes:
        raise ValueError("primary output contains no SHA-256 entries")
    return hashes


def transfer_manifest(cfg: dict[str, Any], files: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """Copy manifest files using the existing E4 QGA guest-file utility."""
    tool = Path(cfg.get("transfer_tool", "experiments/dm8_backup_recovery/scripts/qga_copy_guest_file.py"))
    result = []
    for name, size in files:
        source = str(Path(cfg["backupset"]) / name)
        target = str(Path(cfg["restore_input"]) / name)
        try:
            completed = subprocess.run(
                ["python3", str(tool), cfg["primary_guest"], source, cfg.get("restore_guest", "dm8-restore-c"), target, "--chunk-size", str(cfg.get("chunk_size", 64 * 1024))],
                capture_output=True, text=True, check=False, timeout=int(cfg.get("transfer_timeout", 900)),
            )
        except subprocess.TimeoutExpired as exc:
            raise QgaUnknown(f"transfer timed out for {name}; guest file state unknown; no retry") from exc
        if completed.returncode != 0:
            raise RuntimeError(f"transfer failed for {name}: {completed.stderr.strip()}")
        copied = re.search(r"copied_bytes=(\d+)", completed.stdout)
        if not copied or int(copied.group(1)) != size:
            raise RuntimeError(f"transfer byte count mismatch for {name}")
        result.append({"name": name, "bytes": size, "source": source, "target": target, "output": completed.stdout.strip()})
    return result


def prepare_restore_input(cfg: dict[str, Any]) -> dict[str, Any]:
    return invoke_guest(cfg["restore_guest"],dict(operation="prepare-input",path=cfg["restore_input"],mount=cfg["restore_mount"]),cfg.get("timeout",120))


def verify_target_hashes(cfg: dict[str, Any], hashes: dict[str, str]) -> dict[str, str]:
    """Read target hashes through QGA after transfer; no retry is attempted."""
    paths = " ".join(shlex.quote(str(Path(cfg["restore_input"]) / name)) for name in hashes)
    result = QgaRunner(cfg.get("restore_guest", "dm8-restore-c"), cfg.get("timeout", 900)).run(f"sha256sum {paths}")
    if result.exitcode != 0:
        raise RuntimeError("target SHA-256 command failed")
    observed = parse_hashes(result.stdout)
    if observed != hashes:
        raise RuntimeError("transferred backup SHA-256 mismatch")
    return observed


def collect_source_baseline(cfg: dict[str, Any]) -> dict[str, Any]:
    """Collect six source counts inside PRIMARY, then reuse dm8_verify's baseline builder."""
    pre=invoke_guest(cfg["primary_guest"],dict(operation="sql",credential_file=cfg["credential_file"],username=cfg["username"],db_host=cfg["db_host"],db_port=cfg["db_port"],queries=preflight_queries()),cfg.get("timeout",120))
    parse_primary_preflight(pre["stdout"])
    guest_result=invoke_guest(cfg["primary_guest"],dict(operation="sql",credential_file=cfg["credential_file"],username=cfg["username"],db_host=cfg["db_host"],db_port=cfg["db_port"],queries=six_count_queries()),cfg.get("timeout",120))
    tables = json.loads(Path(cfg.get("tables_config", "config/tables.json")).read_text(encoding="utf-8"))["tables"]
    from dm8_verify.collectors.dm8 import count_baseline_from_output
    baseline = count_baseline_from_output(guest_result.get("stdout", ""),
        dict(host=cfg["db_host"], port=cfg["db_port"], service=cfg["db_service"], schema_name="HR_TEST"), tables, source_type="DATABASE")
    baseline["source_run_id"] = cfg["run_id"]
    return baseline


def compare_row_count_baselines(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare source snapshots at ROW_COUNT level using the existing comparator."""
    from dm8_verify.comparator import compare_baselines
    return compare_baselines(before, after, level="ROW_COUNT")


def restored_count_baseline(counts: dict[str, int]) -> dict[str, Any]:
    """Adapt R1's six-table observation to dm8_verify's baseline shape."""
    tables = {
        name: {"table_name": name, "row_count": value, "collection_status": "PASS", "errors": [],
               "primary_key_fingerprint": None, "data_fingerprint": None,
               "column_metadata": {"status": "NOT_COLLECTED"},
               "constraint_metadata": {"status": "NOT_COLLECTED"},
               "index_metadata": {"status": "NOT_COLLECTED"},
               "aggregate_statistics": {"status": "NOT_COLLECTED"}}
        for name, value in counts.items()
    }
    return {"schema_version": "1.0", "fingerprint_version": "1.0", "source_type": "RESTORED",
            "tables": tables, "total_row_count": sum(counts.values()), "collection_status": "PASS", "errors": []}


def write_report(cfg: dict[str, Any], status: str, details: dict[str, Any]) -> Path:
    report_dir = Path(cfg["report_dir"])
    report_dir.mkdir(parents=True, exist_ok=True)
    run_id = cfg["run_id"]
    report = {"run_id": run_id, "workflow": "I1 B1-primary-to-R1", "status": status, **details}
    path = Path(cfg.get("i1_report_path", report_dir / f"{run_id}-i1.json"))
    if path.exists():
        raise FileExistsError(f"refusing to overwrite I1 report: {path}")
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def execute_r1(cfg: dict[str, Any]) -> int:
    """Invoke the existing R1 runner against the transferred guest directory."""
    restore_path = Path(cfg.get("restore_config", "config/mvp-r1.example.json"))
    restore_cfg = json.loads(restore_path.read_text(encoding="utf-8"))
    restore_cfg["guest"] = cfg.get("restore_guest", "dm8-restore-c")
    restore_cfg["backupset"] = cfg["restore_input"]
    restore_cfg["target"] = cfg["restore_target"]
    restore_cfg["db_name"] = cfg["db_name"]
    restore_cfg["mapping"] = cfg["mapping"]
    restore_cfg["logdir"] = cfg["restore_logdir"]
    restore_cfg["report_dir"] = cfg["r1_report_dir"]
    restore_cfg["run_id"] = cfg["run_id"]
    with tempfile.NamedTemporaryFile("w", suffix="-r1.json", encoding="utf-8") as handle:
        json.dump(restore_cfg, handle); handle.flush()
        return restore.main(["--config", handle.name, "--execute"])


def derived_restore_config(cfg: dict[str, Any]) -> dict[str, Any]:
    restore_path=Path(cfg.get("restore_config","config/mvp-r1.example.json"))
    value=json.loads(restore_path.read_text(encoding="utf-8"))
    value.update(guest="dm8-restore-c",backupset=cfg["restore_input"],target=cfg["restore_target"],
        db_name=cfg["db_name"],mapping=cfg["mapping"],logdir=cfg["restore_logdir"],
        report_dir=cfg["r1_report_dir"],run_id=cfg["run_id"])
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dm8_mvp run")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preflight-only", action="store_true", help="run readonly guest probes and source COUNT only")
    parser.add_argument("--execute", action="store_true", help="explicitly enable live PRIMARY QGA execution")
    args=parser.parse_args(argv)
    source_cfg=json.loads(args.config.read_text(encoding="utf-8"))
    validate_config(source_cfg)
    run_id="i1-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")+"-"+uuid.uuid4().hex[:8]
    cfg=prepare_run_config(source_cfg,run_id)
    cfg["primary_required_bytes"]=required_primary_bytes(cfg)
    cfg["restore_required_bytes"]=required_restore_bytes(cfg)
    try:
        derived_restore=derived_restore_config(cfg)
        restore.validate_config(derived_restore)
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        raise ValueError(f"derived R1 configuration invalid: {exc}") from exc
    if args.dry_run or not (args.execute or args.preflight_only):
        print(build_i1_dry_run(cfg),end="")
        return 0

    details={
        "run_id":run_id,"r1_report_path":str(Path(cfg["r1_report_dir"])/f"{run_id}.json"),
        "i1_report_path":cfg["i1_report_path"],
        "stages":{key:"NOT EXECUTED" for key in ("primary_preflight","restore_preflight","source_before","backup","check","source_after","transfer","restore","row_count_compare")},
        "source_comparison":"NOT EXECUTED","restored_comparison":"NOT EXECUTED"}
    status="BLOCKED"; stage="readonly_preflight"
    try:
        details["primary_probe"]=invoke_guest(cfg["primary_guest"],primary_probe_payload(cfg),cfg.get("timeout",120))
        details["stages"]["primary_preflight"]="PASS"
        details["restore_probe"]=invoke_guest(cfg["restore_guest"],restore_probe_payload(cfg),cfg.get("timeout",120))
        details["stages"]["restore_preflight"]="PASS"
        details["space_budget"]={"primary_available_bytes":details["primary_probe"]["available_bytes"],"primary_required_bytes":cfg["primary_required_bytes"],"restore_available_bytes":details["restore_probe"]["available_bytes"],"restore_required_bytes":cfg["restore_required_bytes"],"historical_restore_bytes":cfg["historical_restore_bytes"],"backup_estimate_bytes":cfg["backup_estimate_bytes"],"logs_reserve_bytes":cfg["logs_reserve_bytes"],"safety_margin_percent":cfg["safety_margin_percent"]}
        stage="source_before"
        source_before=collect_source_baseline(cfg); details["source_before"]=source_before; details["stages"]["source_before"]="PASS"
        if args.preflight_only:
            status="PASS"
        else:
            stage="backup"
            # A lost QGA response cannot establish whether either tool ran.
            details["stages"].update(backup="BLOCKED",check="BLOCKED")
            primary_result=execute_primary(cfg)
            details["primary_execution"]={"pid":primary_result.pid,"exited":primary_result.exited,
                "exitcode":primary_result.exitcode,"stdout":primary_result.stdout,"stderr":primary_result.stderr}
            primary=json.loads(primary_result.stdout)
            if not isinstance(primary,dict):
                raise ValueError("PRIMARY result was not an object")
            details["primary_result"]=primary
            primary_stages=primary.get("stages",{})
            for name in ("backup","check"):
                value=primary_stages.get(name)
                if value in ("PASS","FAIL","BLOCKED","INCOMPLETE","NOT EXECUTED"):
                    details["stages"][name]=value
            if "check" in primary:
                stage="check"
            if primary_result.exitcode != 0:
                raise I1StageError("BLOCKED" if primary.get("status")=="BLOCKED" else "FAIL",
                                  f"PRIMARY guest operation exitcode={primary_result.exitcode}",primary)
            if primary.get("status") != "PASS":
                status=primary.get("status") if primary.get("status") in ("FAIL","BLOCKED","INCOMPLETE") else "FAIL"
                raise I1StageError(status,"PRIMARY backup/CHECK did not PASS: "+str(primary.get("reason",primary.get("status"))),primary)
            if any(details["stages"][name]!="PASS" for name in ("backup","check")):
                raise I1StageError("INCOMPLETE","PRIMARY response lacks successful BACKUP/CHECK stage evidence",primary)
            stage="source_after"
            source_after=collect_source_baseline(cfg); details["source_after"]=source_after
            source_comparison=compare_row_count_baselines(source_before,source_after); details["source_comparison"]=source_comparison
            if source_comparison.get("status") != "PASS":
                status="INCOMPLETE"; details["comparability"]="source COUNT changed; transfer and restore not executed"
            else:
                details["stages"]["source_after"]="PASS"
                stage="transfer"
                manifest=primary.get("manifest","").splitlines()
                files=[]
                for entry in manifest:
                    fields=entry.split()
                    if len(fields)!=2 or not fields[1].isdigit(): raise ValueError("malformed backup manifest")
                    files.append((fields[0],int(fields[1])))
                hashes={}
                for line in primary.get("hashes","").splitlines():
                    fields=line.split()
                    if len(fields)!=2: raise ValueError("malformed SHA-256 manifest")
                    hashes[Path(fields[1]).name]=fields[0]
                if not files or {n for n,_ in files} != set(hashes): raise ValueError("backup file/sha256 sets differ or empty")
                details["manifest"]=files; details["source_sha256"]=hashes
                details["transfer_directory"]=prepare_restore_input(cfg)
                details["transfer"]=transfer_manifest(cfg,files)
                details["target_sha256"]=verify_target_hashes(cfg,hashes)
                details["stages"]["transfer"]="PASS"
                stage="restore"
                # Once dispatched, an absent result is not evidence of non-execution.
                details["stages"]["restore"]="BLOCKED"
                restore_rc=execute_r1(cfg); details["restore_exitcode"]=restore_rc
                r1_path=Path(cfg["r1_report_dir"])/f"{run_id}.json"
                if not r1_path.is_file(): raise RuntimeError("R1 this-run report missing")
                r1=json.loads(r1_path.read_text(encoding="utf-8")); details["r1_report"]=r1
                if r1.get("run_id") != run_id:
                    raise RuntimeError("R1 report run_id mismatch")
                if r1.get("status") in ("FAIL", "BLOCKED", "INCOMPLETE"):
                    r1_status=r1["status"]
                    details["stages"]["restore"]=r1_status
                    raise I1StageError(r1_status,f"R1 exited {restore_rc} with status {r1_status}")
                if restore_rc != 0:
                    raise RuntimeError(f"R1 exited {restore_rc} with status {r1.get('status')}")
                r1_stages=r1.get("stages")
                if (not isinstance(r1_stages,dict) or
                        any(r1_stages.get(name)!="PASS" for name in ("physical_recovery","verification"))):
                    raise I1StageError("INCOMPLETE","R1 PASS lacks matching physical recovery and verification PASS evidence")
                counts=r1.get("counts"); expected={"LOCATIONS","DEPARTMENTS","JOBS","EMPLOYEES","JOB_HISTORY","SALARIES"}
                if r1.get("status")!="PASS" or r1.get("verification")!="ROW_COUNT_OBSERVED" or not isinstance(counts,dict) or set(counts)!=expected or r1.get("comparison")!="NOT EXECUTED":
                    raise ValueError("R1 report missing matching PASS, OPEN/NORMAL six-count evidence")
                details["stages"]["restore"]="PASS"
                stage="row_count_compare"
                compare=compare_row_count_baselines(source_before,restored_count_baseline(counts)); details["restored_comparison"]=compare
                details["stages"]["row_count_compare"]=compare.get("status","INCOMPLETE")
                status="PASS" if compare.get("status")=="PASS" else "FAIL"
    except QgaUnknown as exc:
        status="BLOCKED";details["failure"]={"stage":stage,"type":type(exc).__name__,"reason":str(exc),"guest_outcome":"UNKNOWN; no retry"}
    except I1StageError as exc:
        status=exc.status;details["failure"]={"stage":stage,"type":type(exc).__name__,"reason":str(exc)}
        if exc.guest_result is not None:
            details["failure"]["guest_result"]=exc.guest_result
            if exc.guest_result.get("unknown"):
                details["failure"]["guest_outcome"]="UNKNOWN; no retry"
    except (OSError,RuntimeError,ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
        status=("BLOCKED" if stage in ("readonly_preflight","source_before") else
                "INCOMPLETE" if stage == "source_after" else "FAIL")
        details["failure"]={"stage":stage,"type":type(exc).__name__,"reason":str(exc)}
    if "failure" in details and stage in ("restore", "row_count_compare"):
        details["stages"][stage]=status
    report=write_report(cfg,status,details)
    print(json.dumps({"status":status,"report":str(report),"run_id":run_id},ensure_ascii=False))
    return 0 if status=="PASS" else 3 if status=="INCOMPLETE" else 2 if status=="BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

