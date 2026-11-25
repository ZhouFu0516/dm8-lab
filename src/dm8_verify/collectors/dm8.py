"""Read-only DM8/disql adapter. Live execution is intentionally opt-in."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import Any

from ..fingerprint import canonical_bytes, normalize
from .disql_results import result_lines

BEGIN_PREFIX = "__DM8_VERIFY_BEGIN__"
END_PREFIX = "__DM8_VERIFY_END__"
COUNT_BEGIN_PREFIX = "__DM8_VERIFY_COUNT_BEGIN__"
COUNT_END_PREFIX = "__DM8_VERIFY_COUNT_END__"


def build_disql_script(config: dict[str, Any], tables: list[dict[str, Any]]) -> str:
    """Create a SELECT-only script. Password arrives through stdin, never argv/logs."""
    env_name = config.get("password_env", "DM8_VERIFY_PASSWORD")
    password = os.environ.get(env_name)
    if not password:
        raise ValueError(f"required password environment variable {env_name} is not set")
    username = config.get("username", "")
    host = config.get("host", "")
    port = int(config.get("port", 5236))
    service = config.get("service")
    if not all((username, host, service)) or any(ch in username + host + service for ch in "\r\n\t"):
        raise ValueError("database username, host and service must be configured")
    if not re.fullmatch(r"[A-Za-z0-9_!#$%&*+,.=?^~-]+", password):
        raise ValueError("password contains characters unsupported by safe disql stdin mode; use a compatible credential")
    schema = config.get("schema_name", "HR_TEST")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*", schema):
        raise ValueError("invalid schema identifier")
    lines = [
        "SET ECHO OFF", "SET HEADING OFF", "SET FEEDBACK OFF", "SET PAGESIZE 0",
        "SET LINESIZE 32767", "SET VERIFY OFF", "SET TRIMSPOOL ON", "SET SQLPROMPT ''",
        f"CONNECT {username}/{password}@{host}:{port}",
    ]
    for table in tables:
        name = table["name"].upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", name):
            raise ValueError(f"invalid table identifier: {name}")
        fields = []
        for column in table["columns"]:
            col = column["name"].upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", col):
                raise ValueError(f"invalid column identifier: {col}")
            kind = column["type"].upper()
            text = f"TO_CHAR({col}, 'YYYY-MM-DD')" if kind == "DATE" else f"TO_CHAR({col})"
            fields.append(f"NVL({text}, '\\N')")
        expression = " || CHR(9) || ".join(fields)
        order = ", ".join(table["primary_key"])
        lines.extend([
            f"PROMPT {BEGIN_PREFIX}{name}",
            f"SELECT {expression} FROM {schema}.{name} ORDER BY {order};",
            f"PROMPT {END_PREFIX}{name}",
        ])
    lines.append("EXIT SUCCESS")
    return "\n".join(lines) + "\n"


def build_count_script(config: dict[str, Any], tables: list[dict[str, Any]]) -> str:
    """Build a small COUNT(*)-only script, avoiding fragile full-row serialization."""
    # Reuse the validated credentials and connection properties from the regular
    # script builder, but do not emit its data queries.
    full_script = build_disql_script(config, [])
    lines = full_script.splitlines()[:-1]
    schema = config.get("schema_name", "HR_TEST")
    for table in tables:
        name = table["name"].upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", name):
            raise ValueError(f"invalid table identifier: {name}")
        lines.extend([
            f"PROMPT {COUNT_BEGIN_PREFIX}{name}",
            f"SELECT COUNT(*) FROM {schema}.{name};",
            f"PROMPT {COUNT_END_PREFIX}{name}",
        ])
    lines.append("EXIT SUCCESS")
    return "\n".join(lines) + "\n"


def collect_dm8_counts(config: dict[str, Any], tables: list[dict[str, Any]], source_type: str = "DATABASE") -> dict[str, Any]:
    """Collect real COUNT(*) evidence; every table must yield exactly one integer."""
    from datetime import datetime, timezone
    import uuid

    script = build_count_script(config, tables)
    child_env = os.environ.copy()
    child_env.pop(config.get("password_env", "DM8_VERIFY_PASSWORD"), None)
    try:
        completed = subprocess.run(
            [config.get("disql_path", "disql"), "/nolog"], input=script,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="strict", env=child_env,
            timeout=int(config.get("timeout_seconds", 3600)), check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise RuntimeError(f"failed to execute COUNT(*) query: {type(exc).__name__}: {exc}") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"disql exited with status {completed.returncode}; database output suppressed")
    return count_baseline_from_output(completed.stdout, config, tables, source_type)


def count_baseline_from_output(output: str, config: dict, tables: list[dict], source_type: str = 'DATABASE') -> dict:
    """Shared strict parser/baseline builder for local and QGA guest collection."""
    from datetime import datetime, timezone
    import uuid

    counts: dict[str, int] = {}
    current: str | None = None
    values: list[str] = []
    expected = {table["name"].upper() for table in tables}
    for line in result_lines(output):
        if line.startswith(COUNT_BEGIN_PREFIX):
            if current is not None:
                raise RuntimeError('nested or unterminated COUNT section')
            current = line[len(COUNT_BEGIN_PREFIX):].strip().upper()
            if current not in expected or current in counts:
                raise RuntimeError(f"unexpected or duplicate COUNT(*) section: {current}")
            values = []
            continue
        if line.startswith(COUNT_END_PREFIX):
            name = line[len(COUNT_END_PREFIX):].strip().upper()
            if current != name:
                raise RuntimeError(f"mismatched COUNT(*) section end: {name}")
            if len(values) != 1 or not re.fullmatch(r"\d+", values[0]):
                raise RuntimeError(f"COUNT(*) failed or returned invalid output for {name}")
            counts[name] = int(values[0])
            current = None
            continue
        if current is not None and line:
            # Disql emits diagnostics as text too. Requiring exactly one numeric
            # row per query prevents a SQL error from masquerading as an empty table.
            values.append(re.sub(r'^1\s+(?=\d+$)', '', line))
    if current is not None or counts.keys() != expected:
        missing = expected - counts.keys()
        raise RuntimeError(f"COUNT(*) output incomplete; missing tables: {', '.join(sorted(missing))}")

    results = {
        name: {
            "table_name": name, "row_count": counts[name],
            "primary_key_fingerprint": None, "data_fingerprint": None,
            "column_metadata": {"status": "NOT_COLLECTED"},
            "constraint_metadata": {"status": "NOT_COLLECTED"},
            "index_metadata": {"status": "NOT_COLLECTED"},
            "aggregate_statistics": {"status": "NOT_COLLECTED"},
            "collection_status": "PASS", "errors": [],
        }
        for name in sorted(counts)
    }
    return {
        "schema_version": "1.0", "fingerprint_version": "1.0",
        "run_id": str(uuid.uuid4()), "collection_time": datetime.now(timezone.utc).isoformat(),
        "source_type": source_type,
        "source_identifier": f"{config.get('host')}:{config.get('port', 5236)}/{config.get('service')}",
        "database_version": {"status": "NOT_COLLECTED", "value": None},
        "schema_name": config.get("schema_name", "HR_TEST"),
        "validation_level": "ROW_COUNT", "collection_status": "PASS",
        "tables": results, "total_row_count": sum(counts.values()), "errors": [],
        "notes": ["ROW_COUNT proves table-level row counts only; it does not prove content equality."],
    }


def parse_disql_output(output: str, table_names: list[str]) -> dict[str, list[list[str | None]]]:
    """Parse the explicitly delimited tabular mode used by build_disql_script."""
    parsed: dict[str, list[list[str | None]]] = {}
    current: str | None = None
    expected = set(table_names)
    for raw in output.splitlines():
        line = raw.strip("\r")
        if line.startswith(BEGIN_PREFIX):
            current = line[len(BEGIN_PREFIX):].strip().upper()
            if current not in expected or current in parsed:
                raise ValueError(f"unexpected or duplicate disql result section: {current}")
            parsed[current] = []
            continue
        if line.startswith(END_PREFIX):
            name = line[len(END_PREFIX):].strip().upper()
            if current != name:
                raise ValueError(f"mismatched disql section end: {name}")
            current = None
            continue
        if current is None or not line.strip():
            continue
        parsed[current].append([None if value == r"\N" else value for value in line.split("\t")])
    if current is not None:
        raise ValueError(f"unterminated disql output section: {current}")
    missing = expected - parsed.keys()
    if missing:
        raise ValueError(f"disql output missing result sections: {', '.join(sorted(missing))}")
    return parsed


def stream_disql_output(config: dict[str, Any], script: str, table_names: list[str]):
    """Yield rows as disql emits them; captured database output is never buffered in RAM."""
    executable = config.get("disql_path", "disql")
    timeout = int(config.get("timeout_seconds", 3600))
    try:
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as error_output:
            child_env = os.environ.copy()
            child_env.pop(config.get("password_env", "DM8_VERIFY_PASSWORD"), None)
            process = subprocess.Popen(
                [executable, "/nolog"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=error_output, text=True, encoding="utf-8", errors="strict",
                env=child_env,
            )
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(script)
            process.stdin.close()
            current: str | None = None
            seen: set[str] = set()
            expected = set(table_names)
            for raw in process.stdout:
                line = raw.rstrip("\r\n")
                if line.startswith(BEGIN_PREFIX):
                    current = line[len(BEGIN_PREFIX):].strip().upper()
                    if current not in expected or current in seen:
                        process.kill()
                        raise RuntimeError(f"unexpected or duplicate disql result section: {current}")
                    seen.add(current)
                    continue
                if line.startswith(END_PREFIX):
                    name = line[len(END_PREFIX):].strip().upper()
                    if current != name:
                        process.kill()
                        raise RuntimeError(f"mismatched disql section end: {name}")
                    current = None
                    continue
                if current is not None and line.strip():
                    yield current, [None if value == r"\N" else value for value in line.split("\t")]
            code = process.wait(timeout=timeout)
            if current is not None:
                raise RuntimeError(f"unterminated disql output section: {current}")
            missing = expected - seen
            if missing:
                raise RuntimeError(f"disql output missing result sections: {', '.join(sorted(missing))}")
            if code != 0:
                raise RuntimeError(f"disql exited with status {code}; output suppressed to protect credentials")
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        try:
            process.kill()
        except (UnboundLocalError, AttributeError, OSError):
            pass
        raise RuntimeError(f"failed to execute disql: {type(exc).__name__}: {exc}") from exc


def collect_dm8(config: dict[str, Any], tables: list[dict[str, Any]], source_type: str = "DATABASE", level: str = "DATA_ONLY") -> dict[str, Any]:
    """Collect data fingerprints. DM8 catalog metadata remains NOT_COLLECTED until version-specific SQL is verified."""
    if level == "ROW_COUNT":
        return collect_dm8_counts(config, tables, source_type)
    if level not in {"DATA_ONLY", "FULL"}:
        raise ValueError(f"unsupported collection level: {level}")
    from datetime import datetime, timezone
    import uuid
    from ..fingerprint import MultisetDigest, FINGERPRINT_VERSION

    script = build_disql_script(config, tables)
    results: dict[str, Any] = {}
    total = 0
    errors: list[str] = []
    state: dict[str, dict[str, Any]] = {}
    for table in tables:
        name = table["name"].upper()
        state[name] = {
            "data": MultisetDigest(f"DATA:{name}"), "pk": MultisetDigest(f"PK:{name}"),
            "count": 0, "key_indexes": [next(i for i, col in enumerate(table["columns"]) if col["name"] == key) for key in table["primary_key"]],
            "columns": table["columns"], "primary_key": table["primary_key"], "previous_key": None,
        }
    for name, row in stream_disql_output(config, script, list(state)):
        item = state[name]
        if len(row) != len(item["columns"]):
            raise RuntimeError(f"{name} row {item['count'] + 1}: expected {len(item['columns'])} columns, got {len(row)}")
        normalized = [normalize(value, column) for value, column in zip(row, item["columns"])]
        item["data"].add(canonical_bytes(normalized))
        key = [normalized[index] for index in item["key_indexes"]]
        if any(value is None for value in key):
            raise RuntimeError(f"{name} row {item['count'] + 1}: NULL primary key")
        key_payload = canonical_bytes(key)
        if key_payload == item["previous_key"]:
            raise RuntimeError(f"{name} row {item['count'] + 1}: duplicate primary key")
        item["previous_key"] = key_payload
        item["pk"].add(key_payload)
        item["count"] += 1
    for table in tables:
        name = table["name"].upper()
        item = state[name]
        count = item["count"]
        total += count
        results[name] = {
            "table_name": name, "row_count": count, "primary_key_columns": table["primary_key"],
            "primary_key_fingerprint": item["pk"].as_dict(), "data_fingerprint": item["data"].as_dict(),
            "column_metadata": {"status": "NOT_COLLECTED", "columns": None},
            "constraint_metadata": {"status": "NOT_COLLECTED", "items": None},
            "index_metadata": {"status": "NOT_COLLECTED", "items": None},
            "aggregate_statistics": {"status": "NOT_COLLECTED"}, "collection_status": "PASS", "errors": [],
        }
    return {
        "schema_version": "1.0", "fingerprint_version": FINGERPRINT_VERSION,
        "run_id": str(uuid.uuid4()), "collection_time": datetime.now(timezone.utc).isoformat(),
        "source_type": source_type, "source_identifier": f"{config.get('host')}:{config.get('port', 5236)}/{config.get('service')}",
        "database_version": {"status": "NOT_COLLECTED", "value": None},
        "schema_name": config.get("schema_name", "HR_TEST"), "collection_status": "PASS",
        "tables": results, "total_row_count": total, "errors": errors,
        "notes": ["DM8 live execution was not performed unless this collector was explicitly invoked.", "Catalog metadata queries are version-specific and remain NOT_COLLECTED."]
    }
