from __future__ import annotations

import argparse
import inspect
import json
import os
import shlex
import uuid
from datetime import datetime, timezone
from pathlib import Path
import re

from .runner import QgaRunner
from dm8_verify.collectors.disql_results import check_sql_errors, result_lines


COUNT_TABLES = ("LOCATIONS", "DEPARTMENTS", "JOBS", "EMPLOYEES", "JOB_HISTORY", "SALARIES")
MVP_GUEST = "dm8-restore-c"
MOUNT_ROOTS = tuple(Path(p) for p in ("/data/mvp", "/data/mvp40", "/data/mvp60"))
PROTECTED_ROOTS = tuple(Path(p) for p in ("/data/restore-c", "/data/e7", "/data/dm8"))


def verification_lines(text: str) -> list[str]:
    """Normalize terminal formatting without accepting arbitrary success text."""
    text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    return list(result_lines(text.replace('\r\n', '\n').replace('\r', '\n')))


def parse_restore_status(text: str, expected_instance: str) -> dict[str, str]:
    """Require one bounded V$INSTANCE result, in instance/status/mode order."""
    lines = verification_lines(text)
    begin, end = '__R1_STATUS_BEGIN__', '__R1_STATUS_END__'
    if lines.count(begin) != 1 or lines.count(end) != 1:
        raise ValueError('missing or duplicate R1 status boundary')
    first, last = lines.index(begin), lines.index(end)
    if last != first + 2:
        raise ValueError('missing or ambiguous R1 status result')
    match = re.fullmatch(r'(?:1\s+)?([A-Za-z][A-Za-z0-9_-]*)\s+([A-Za-z]+)\s+([A-Za-z]+)',
                         lines[first + 1])
    if not match:
        raise ValueError('malformed R1 status result')
    instance, status, mode = match.groups()
    if instance.upper() != expected_instance.upper() or (status.upper(), mode.upper()) != ('OPEN', 'NORMAL'):
        raise ValueError('restored instance is not the expected OPEN/NORMAL instance')
    return dict(instance=instance, status=status.upper(), mode=mode.upper())


def parse_count_output(text: str) -> dict[str, int]:
    """Parse explicitly labelled COUNT output; never infer table order."""
    lines = verification_lines(text)
    values = {}
    for table in COUNT_TABLES:
        marker = f"__COUNT_{table}__"
        if lines.count(marker) != 1:
            raise ValueError(f"missing or duplicate marker for {table}")
        section = []
        for line in lines[lines.index(marker) + 1:]:
            if line.startswith(('__COUNT_', 'MVP_')):
                break
            section.append(line)
        match = re.fullmatch(r'1\s+(\d+)', section[0]) if len(section) == 1 else None
        if not match:
            raise ValueError(f"missing, invalid or ambiguous COUNT for {table}")
        values[table] = int(match.group(1))
    return values


def recovery_verification_output(text: str) -> str:
    """Require unique, ordered whole-line completion evidence before acceptance."""
    verification_lines(text)  # Reject SQL errors even outside the query sections.
    lines = [line.strip() for line in text.splitlines()]
    markers = ('MVP_PHYSICAL_RECOVERY_PASS', 'STAGE START-VERIFY', 'MVP_RECOVERY_STAGES_PASS')
    if any(lines.count(marker) != 1 for marker in markers):
        raise ValueError('missing or duplicate R1 completion evidence')
    physical, verify, finished = (lines.index(marker) for marker in markers)
    if not physical < verify < finished:
        raise ValueError('R1 completion evidence is out of order')
    return '\n'.join(lines[verify + 1:finished])


def build_verification_gate() -> str:
    """Embed the same parser and its actual dependencies; no guest package install."""
    source = '\n\n'.join(inspect.getsource(function) for function in (
        check_sql_errors, result_lines, verification_lines, parse_restore_status, parse_count_output))
    return '''if python3 - "$LOGDIR/verify.log" "$INSTANCE_NAME" <<'PY_R1_VERIFY'
from __future__ import annotations
import re, sys
''' + f'COUNT_TABLES = {COUNT_TABLES!r}\n' + source + '''
try:
    with open(sys.argv[1]) as stream:
        output = stream.read()
    parse_restore_status(output, sys.argv[2])
    parse_count_output(output)
except (OSError, ValueError) as exc:
    print('FAIL:R1 verification: ' + str(exc))
    raise SystemExit(10)
PY_R1_VERIFY
then
    :
else
    cat "$LOGDIR/verify.log"
    echo 'FAIL:R1 state or COUNT verification'
    exit 10
fi
'''


def build_completion_footer() -> str:
    """Keep the final stage marker separate from a trailing DIsql prompt."""
    return '''cat "$LOGDIR/verify.log"
printf '\n'
echo 'MVP_RECOVERY_STAGES_PASS'
'''


def validate_mapping_text(text: str, target: str) -> list[str]:
    """Validate all path/file assignments in a MAPPED FILE without touching disk."""
    root = _safe_path(target, "target")
    paths = []
    assignment = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*(?:path|file)[A-Za-z0-9_]*\s*=\s*(.*?)\s*$", re.I)
    for number, line in enumerate(text.splitlines(), 1):
        match = assignment.match(line)
        if not match:
            continue
        raw = match.group(1).strip()
        if not raw:
            continue
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            value = raw[1:-1]
        elif raw[:1] in "\"'" or raw[-1:] in "\"'":
            raise ValueError(f"malformed mapping path on line {number}")
        else:
            value = raw
        if not value.startswith("/") or any(ord(c) < 32 for c in value):
            raise ValueError(f"unsafe mapping path on line {number}")
        normalized = Path(os.path.normpath(value))
        if root not in normalized.parents:
            raise ValueError(f"mapping path escapes target on line {number}")
        paths.append(str(normalized))
    if not paths:
        raise ValueError("mapping contains no writable paths")
    return paths


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    validate_config(cfg)
    return cfg


def validate_config(cfg: dict) -> None:
    required = ("guest", "backupset", "target", "mapping")
    missing = [key for key in required if not cfg.get(key)]
    if missing:
        raise ValueError(f"missing config keys: {', '.join(missing)}")
    if cfg.get("guest") != MVP_GUEST:
        raise ValueError(f"restore guest must be {MVP_GUEST}")
    target = _safe_path(cfg["target"], "target")
    mount = next((root for root in MOUNT_ROOTS if root in target.parents), None)
    if mount is None or target == mount:
        raise ValueError("target must be a child of a dedicated MVP mount")
    if cfg.get("db_name") and cfg["db_name"] != target.name:
        raise ValueError("db_name must equal target directory name")
    for key in ("mapping", "logdir"):
        raw = cfg.get(key)
        if key == "logdir" and not raw:
            raw = str(target.parent / "mvp-logs")
        value = _safe_path(raw, key)
        if mount not in value.parents and value != mount:
            raise ValueError(f"{key} must remain on the target MVP mount")
    if cfg.get("instance_name") and not re.fullmatch(r"[A-Za-z0-9_-]+", str(cfg["instance_name"])):
        raise ValueError("invalid instance_name")
    port = int(cfg.get("port", 15241))
    if not 1024 <= port <= 65535:
        raise ValueError("target port must be an unprivileged TCP port")
    for key in ("guest", "db_name", "instance_name"):
        if key in cfg and not re.fullmatch(r"[A-Za-z0-9_-]+", str(cfg[key])):
            raise ValueError(f"invalid {key}")


def _safe_path(value: str | None, label: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError(f"{label} must be an absolute path")
    if any(part in (".", "..") for part in value.split("/")):
        raise ValueError(f"{label} contains unsafe path components")
    if any(ord(c) < 32 or c in "'\";|&$`\n\r" for c in value):
        raise ValueError(f"{label} contains unsafe characters")
    path = Path(value)
    if any(path == root or root in path.parents for root in PROTECTED_ROOTS):
        raise ValueError(f"{label} overlaps protected assets")
    return path


def build_script(cfg: dict) -> str:
    # All destructive work is guarded by an empty, dedicated target directory.
    validate_config(cfg)
    guest = cfg["guest"]
    backup = _safe_path(cfg["backupset"], "backupset")
    target = _safe_path(cfg["target"], "target")
    mapping = _safe_path(cfg["mapping"], "mapping")
    logdir = _safe_path(cfg.get("logdir", str(target.parent / "mvp-logs")), "logdir")
    port = int(cfg.get("port", 15241))
    db_name = cfg.get("db_name", target.name)
    instance_name = cfg.get("instance_name", db_name)
    q = shlex.quote
    return f'''set -u
umask 077
GUEST={q(guest)}; BACKUP={q(str(backup))}; TARGET={q(str(target))}; MAPPING={q(str(mapping))}; LOGDIR={q(str(logdir))}; PORT={port}; DB_NAME={q(db_name)}; INSTANCE_NAME={q(instance_name)}
DMR=/opt/dmdbms/bin/dmrman
DMINIT=/opt/dmdbms/bin/dminit
DMINI="$TARGET/dm.ini"
fail() {{ echo "FAIL:$*"; exit 10; }}
safe_mapping() {{
  double_quoted='^"(.*)"$'
  single_quoted="^'(.*)'$"
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*(path|file)[A-Za-z0-9_]*[[:space:]]*=[[:space:]]*(.*)$ ]] || continue
    value="${{BASH_REMATCH[2]}}"
    [ -z "$value" ] && continue
    if [[ "$value" =~ $double_quoted ]]; then value="${{BASH_REMATCH[1]}}"; elif [[ "$value" =~ $single_quoted ]]; then value="${{BASH_REMATCH[1]}}"; fi
    [[ "$value" = /* ]] || fail "mapping path is not absolute"
    resolved=$(realpath -m -- "$value") || fail "cannot resolve mapping path"
    case "$resolved" in "$TARGET"/*) ;; *) fail "mapping path escapes target: $value";; esac
  done < "$MAPPING"
}}
safe_guest_path() {{
  case "$1" in /data/mvp/*|/data/mvp40/*|/data/mvp60/*) ;; *) fail "path outside MVP mounts: $1";; esac
  [ "$(realpath -m -- "$1")" = "$1" ] || fail "path normalization mismatch: $1"
}}
safe_guest_path "$TARGET"; safe_guest_path "$MAPPING"; safe_guest_path "$LOGDIR"
test -d "$BACKUP" || fail "backupset missing"
find "$BACKUP" -maxdepth 1 -type f -name '*.bak' -print -quit | grep -q . || fail "bak missing"
find "$BACKUP" -maxdepth 1 -type f -name '*.meta' -print -quit | grep -q . || fail "meta missing"
test ! -e "$TARGET" || fail "target already exists"
test ! -e "$LOGDIR" || fail "log directory already exists; refusing overwrite"
mkdir -p "$(dirname "$TARGET")" || fail "target parent"
mkdir -p "$LOGDIR"
echo 'STAGE CHECK'
runuser -u dmdba -- "$DMR" USE_AP=2 CTLSTMT="CHECK BACKUPSET '$BACKUP' LEVEL 1" >"$LOGDIR/check.log" 2>&1 || {{ cat "$LOGDIR/check.log"; fail check; }}
echo 'STAGE DMINIT'
PW="Aa9$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 17)"
runuser -u dmdba -- "$DMINIT" PATH="$(dirname "$TARGET")" DB_NAME="$DB_NAME" INSTANCE_NAME="$INSTANCE_NAME" PORT_NUM="$PORT" PAGE_SIZE=8 EXTENT_SIZE=16 CASE_SENSITIVE=Y CHARSET=0 MAL_FLAG=0 ARCH_FLAG=0 SYSDBA_PWD="$PW" SYSAUDITOR_PWD="$PW" >"$LOGDIR/dminit.log" 2>&1 || {{ cat "$LOGDIR/dminit.log"; fail dminit; }}
test -f "$DMINI" || fail 'dm.ini missing'
echo 'STAGE DUMP-MAPPING'
runuser -u dmdba -- "$DMR" USE_AP=2 CTLSTMT="DUMP BACKUPSET '$BACKUP' DATABASE '$DMINI' MAPPED FILE '$MAPPING'" >"$LOGDIR/dump.log" 2>&1 || {{ cat "$LOGDIR/dump.log"; fail dump; }}
test -s "$MAPPING" || fail 'mapping missing'
safe_mapping
echo 'STAGE PRE-START-ISOLATION'
grep -Eq "^[[:space:]]*PORT_NUM[[:space:]]*=[[:space:]]*$PORT([[:space:]#]|$)" "$DMINI" || fail 'target port mismatch after restore'
grep -Eq "^[[:space:]]*INSTANCE_NAME[[:space:]]*=[[:space:]]*$INSTANCE_NAME([[:space:]#]|$)" "$DMINI" || fail 'target instance name mismatch after restore'
grep -Eq '^[[:space:]]*MAL_INI[[:space:]]*=[[:space:]]*0([[:space:]#]|$)' "$DMINI" || fail 'MAL must be disabled before start'
grep -Eq '^[[:space:]]*ARCH_INI[[:space:]]*=[[:space:]]*0([[:space:]#]|$)' "$DMINI" || fail 'ARCH must be disabled before start'
if ss -ltnH | awk '{{print $4}}' | grep -Eq "(:|\\])$PORT$"; then fail 'target port is already listening'; fi
echo 'STAGE RESTORE'
runuser -u dmdba -- "$DMR" USE_AP=2 CTLSTMT="RESTORE DATABASE '$DMINI' REUSE DMINI FROM BACKUPSET '$BACKUP' WITH BACKUPDIR '$(dirname "$BACKUP")' MAPPED FILE '$MAPPING'" >"$LOGDIR/restore.log" 2>&1 || {{ cat "$LOGDIR/restore.log"; fail restore; }}
echo 'STAGE RECOVER'
runuser -u dmdba -- "$DMR" USE_AP=2 CTLSTMT="RECOVER DATABASE '$DMINI' FROM BACKUPSET '$BACKUP'" >"$LOGDIR/recover.log" 2>&1 || {{ cat "$LOGDIR/recover.log"; fail recover; }}
runuser -u dmdba -- "$DMR" USE_AP=2 CTLSTMT="RECOVER DATABASE '$DMINI' UPDATE DB_MAGIC" >"$LOGDIR/update-magic.log" 2>&1 || {{ cat "$LOGDIR/update-magic.log"; fail update_magic; }}
echo 'MVP_PHYSICAL_RECOVERY_PASS'
echo 'STAGE START-VERIFY'
sed -i -E 's/^[[:space:]]*MAL_INI[[:space:]]*=.*/MAL_INI = 0/; s/^[[:space:]]*ARCH_INI[[:space:]]*=.*/ARCH_INI = 0/; s/^[[:space:]]*ENABLE_LOCAL_OSAUTH[[:space:]]*=.*/ENABLE_LOCAL_OSAUTH = 1/; s/^[[:space:]]*ALTER_MODE_STATUS[[:space:]]*=.*/ALTER_MODE_STATUS = 2/' "$DMINI" || fail 'isolation flags'
sed -i -E "s/^[[:space:]]*PORT_NUM[[:space:]]*=.*/PORT_NUM = $PORT/" "$DMINI" || fail 'target port'
grep -Eq "^[[:space:]]*PORT_NUM[[:space:]]*=[[:space:]]*$PORT([[:space:]#]|$)" "$DMINI" || fail 'final port check'
grep -Eq "^[[:space:]]*INSTANCE_NAME[[:space:]]*=[[:space:]]*$INSTANCE_NAME([[:space:]#]|$)" "$DMINI" || fail 'final instance name check'
grep -Eq '^[[:space:]]*MAL_INI[[:space:]]*=[[:space:]]*0([[:space:]#]|$)' "$DMINI" || fail 'final MAL check'
grep -Eq '^[[:space:]]*ARCH_INI[[:space:]]*=[[:space:]]*0([[:space:]#]|$)' "$DMINI" || fail 'final ARCH check'
if ss -ltnH | awk '{{print $4}}' | grep -Eq "(:|\\])$PORT$"; then fail 'target port is already listening'; fi
runuser -u dmdba -- /opt/dmdbms/bin/dmserver "$DMINI" >"$LOGDIR/dmserver.log" 2>&1 &
SERVER_PID=$!
ready=0
for _ in $(seq 1 120); do
  grep -q 'SYSTEM IS READY' "$LOGDIR/dmserver.log" && ready=1 && break
  kill -0 "$SERVER_PID" 2>/dev/null || break
  sleep 1
done
test "$ready" = 1 || {{ cat "$LOGDIR/dmserver.log"; fail 'dmserver not ready'; }}
{{
  echo "CONNECT /@LOCALHOST:$PORT AS SYSDBA"
  echo 'ALTER DATABASE NORMAL;'
  echo 'ALTER DATABASE OPEN;'
  echo 'SET HEADING OFF'
  echo 'SET FEEDBACK OFF'
  echo 'SET ECHO OFF'
  echo 'PROMPT __R1_STATUS_BEGIN__'
  echo 'SELECT INSTANCE_NAME, STATUS$, MODE$ FROM V$INSTANCE;'
  echo 'PROMPT __R1_STATUS_END__'
  echo 'PROMPT __COUNT_LOCATIONS__'; echo 'SELECT COUNT(*) FROM HR_TEST.LOCATIONS;'
  echo 'PROMPT __COUNT_DEPARTMENTS__'; echo 'SELECT COUNT(*) FROM HR_TEST.DEPARTMENTS;'
  echo 'PROMPT __COUNT_JOBS__'; echo 'SELECT COUNT(*) FROM HR_TEST.JOBS;'
  echo 'PROMPT __COUNT_EMPLOYEES__'; echo 'SELECT COUNT(*) FROM HR_TEST.EMPLOYEES;'
  echo 'PROMPT __COUNT_JOB_HISTORY__'; echo 'SELECT COUNT(*) FROM HR_TEST.JOB_HISTORY;'
  echo 'PROMPT __COUNT_SALARIES__'; echo 'SELECT COUNT(*) FROM HR_TEST.SALARIES;'
  echo 'EXIT;'
}} | runuser -u dmdba -- /opt/dmdbms/bin/disql /nolog >"$LOGDIR/verify.log" 2>&1 || {{ cat "$LOGDIR/verify.log"; fail verify; }}
{build_verification_gate()}
echo 'MVP_VERIFY_LOG='$LOGDIR/verify.log
{build_completion_footer()}
'''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true", help="explicitly enable live restore execution")
    parser.add_argument("--i1-run-id", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    script = build_script(cfg)
    if args.dry_run or not args.execute:
        print(script)
        return 0
    try:
        result = QgaRunner(cfg["guest"], cfg.get("timeout", 900)).run(script)
        if not result.exited or result.exitcode is None:
            from .runner import QgaUnknown
            raise QgaUnknown('R1 guest execution has no confirmed final exit code; no retry')
        exitcode = result.exitcode
        stdout, stderr = result.stdout, result.stderr
        counts = None
        status = "FAIL"
        if result.exitcode == 0 and "MVP_RECOVERY_STAGES_PASS" in [line.strip() for line in stdout.splitlines()]:
            try:
                verification_lines(stderr)
                output = recovery_verification_output(stdout)
                parse_restore_status(output, cfg.get("instance_name", cfg.get("db_name", Path(cfg["target"]).name)))
                counts = parse_count_output(output)
                status = "PASS"
            except ValueError as exc:
                status = "INCOMPLETE"
                stderr = (stderr + "\n" if stderr else "") + str(exc)
    except Exception as exc:
        from .runner import QgaUnknown
        status = "BLOCKED" if isinstance(exc, QgaUnknown) else "FAIL"
        exitcode = None
        stdout, stderr = "", f"{type(exc).__name__}: {exc}"
        counts = None
    print(stdout, end="")
    if stderr:
        print(stderr, end="")
    report_dir = Path(cfg.get("report_dir", "outputs/mvp-r1"))
    report_dir.mkdir(parents=True, exist_ok=True)
    run_id = cfg.get("run_id") or (
        f"{args.i1_run_id}-r1" if args.i1_run_id else
        datetime.now(timezone.utc).strftime("r1-%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8]
    )
    report = {
        "run_id": run_id,
        "workflow": "R1 historical E1 physical restore",
        "guest": cfg["guest"],
        "backupset": cfg["backupset"],
        "target": cfg["target"],
        "exitcode": exitcode,
        "status": status,
        "verification": "ROW_COUNT_OBSERVED" if counts else "NOT EXECUTED",
        "comparison": "NOT EXECUTED",
        "counts": counts,
        "stdout": stdout,
        "stderr": stderr,
        "stages": {
            "physical_recovery": (
                "PASS" if [line.strip() for line in stdout.splitlines()].count("MVP_PHYSICAL_RECOVERY_PASS") == 1 else
                "BLOCKED" if status == "BLOCKED" else
                "FAIL" if any(line in ("STAGE RESTORE", "STAGE RECOVER") for line in stdout.splitlines()) else
                "NOT EXECUTED"
            ),
            "verification": status if [line.strip() for line in stdout.splitlines()].count("STAGE START-VERIFY") == 1 else
                            "BLOCKED" if status == "BLOCKED" else "NOT EXECUTED",
        },
    }
    (report_dir / f"{run_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if status == "PASS":
        return 0
    return exitcode or (3 if status == "INCOMPLETE" else 1)


if __name__ == "__main__":
    raise SystemExit(main())

