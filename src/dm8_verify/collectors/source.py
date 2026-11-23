"""Streaming collector for the synthetic HR source files."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any
import uuid

from ..fingerprint import FINGERPRINT_VERSION, MultisetDigest, canonical_bytes, normalize

SCHEMA_VERSION = "1.0"


def load_config(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load table configuration {path}: {exc}") from exc
    if not isinstance(result.get("tables"), list) or not result["tables"]:
        raise ValueError("table configuration must contain a non-empty tables array")
    for table in result["tables"]:
        if not table.get("name") or not table.get("columns") or not table.get("primary_key"):
            raise ValueError("each table requires name, columns and primary_key")
        names = [column["name"] for column in table["columns"]]
        if len(names) != len(set(names)) or any(key not in names for key in table["primary_key"]):
            raise ValueError(f"invalid column or primary key definition for {table['name']}")
    return result


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(table: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "SOURCE_DDL",
        "items": table["columns"],
    }


def collect_source(input_dir: Path, config_path: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    config = load_config(config_path)
    now = datetime.now(timezone.utc)
    baseline: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "fingerprint_version": FINGERPRINT_VERSION,
        "run_id": str(uuid.uuid4()),
        "collection_time": now.isoformat(),
        "source_type": "SOURCE",
        "source_identifier": str(input_dir.resolve()),
        "database_version": {"status": "NOT_COLLECTED", "value": None},
        "schema_name": config.get("schema_name", "HR_TEST"),
        "collection_status": "PASS",
        "tables": {},
        "errors": [],
        "input_files": {},
        "manifest_check": {"status": "NOT_COLLECTED", "details": []},
        "notes": ["SOURCE evidence describes local source files, never a live database or restored database."],
    }
    expected: dict[str, Any] = {}
    if manifest_path and manifest_path.exists():
        try:
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected = {item["table"].upper(): item for item in raw_manifest.get("tables", [])}
            baseline["manifest_check"] = {"status": "PASS", "details": []}
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            baseline["manifest_check"] = {"status": "ERROR", "details": [str(exc)]}
            baseline["errors"].append(f"invalid manifest: {exc}")
    elif manifest_path:
        baseline["manifest_check"] = {"status": "ERROR", "details": [f"manifest not found: {manifest_path}"]}
        baseline["errors"].append(f"manifest not found: {manifest_path}")

    fd, temp_name = tempfile.mkstemp(prefix="dm8-verify-pk-", suffix=".sqlite")
    import os
    os.close(fd)
    try:
        with sqlite3.connect(temp_name) as key_db:
            key_db.execute("PRAGMA journal_mode=OFF")
            key_db.execute("PRAGMA synchronous=OFF")
            key_db.execute("CREATE TABLE keys (table_name TEXT NOT NULL, key BLOB NOT NULL, PRIMARY KEY(table_name, key)) WITHOUT ROWID")
            total_rows = 0
            for table in config["tables"]:
                name = table["name"].upper()
                path = input_dir / table["file"]
                result: dict[str, Any] = {
                    "table_name": name,
                    "row_count": None,
                    "primary_key_columns": table["primary_key"],
                    "primary_key_fingerprint": None,
                    "data_fingerprint": None,
                    "column_metadata": _metadata(table),
                    "constraint_metadata": {"status": "SOURCE_DDL", "items": table.get("constraints", [])},
                    "index_metadata": {"status": "SOURCE_DDL", "items": {"status": "NOT_SEPARATELY_DECLARED"}},
                    "aggregate_statistics": {},
                    "collection_status": "PASS",
                    "errors": [],
                    "input_file": str(path),
                    "input_sha256": None,
                }
                baseline["tables"][name] = result
                if not path.is_file():
                    result["collection_status"] = "ERROR"
                    result["errors"].append(f"input file missing: {path}")
                    baseline["errors"].append(f"{name}: input file missing: {path}")
                    baseline["collection_status"] = "ERROR"
                    continue
                try:
                    file_sha = _file_hash(path)
                    baseline["input_files"][path.name] = {"sha256": file_sha, "bytes": path.stat().st_size}
                    result["input_sha256"] = file_sha
                    data_digest = MultisetDigest(f"DATA:{name}")
                    key_digest = MultisetDigest(f"PK:{name}")
                    count = 0
                    numeric_sums = {c["name"]: Decimal(0) for c in table["columns"] if c["type"].upper() in {"INT", "INTEGER", "BIGINT", "DECIMAL", "NUMERIC"}}
                    numeric_counts = {key: 0 for key in numeric_sums}
                    insert_batch: list[tuple[str, bytes]] = []
                    with path.open("r", encoding="utf-8", newline="") as source:
                        reader = csv.reader(source, delimiter="|", quoting=csv.QUOTE_NONE, strict=True)
                        for line_number, values in enumerate(reader, start=1):
                            if len(values) != len(table["columns"]):
                                raise ValueError(f"line {line_number}: expected {len(table['columns'])} fields, got {len(values)}")
                            normalized = [normalize(value, column) for value, column in zip(values, table["columns"])]
                            row_payload = canonical_bytes(normalized)
                            data_digest.add(row_payload)
                            key_values = [normalized[next(i for i, col in enumerate(table["columns"]) if col["name"] == key)] for key in table["primary_key"]]
                            if any(value is None for value in key_values):
                                raise ValueError(f"line {line_number}: NULL primary key")
                            key_payload = canonical_bytes(key_values)
                            key_digest.add(key_payload)
                            insert_batch.append((name, key_payload))
                            count += 1
                            for column, value in zip(table["columns"], normalized):
                                cname = column["name"]
                                if cname in numeric_sums and value is not None:
                                    numeric_sums[cname] += Decimal(str(value))
                                    numeric_counts[cname] += 1
                            if len(insert_batch) >= 10000:
                                try:
                                    key_db.executemany("INSERT INTO keys(table_name,key) VALUES (?,?)", insert_batch)
                                except sqlite3.IntegrityError as exc:
                                    raise ValueError(f"duplicate primary key detected by row {line_number}") from exc
                                insert_batch.clear()
                    if insert_batch:
                        try:
                            key_db.executemany("INSERT INTO keys(table_name,key) VALUES (?,?)", insert_batch)
                        except sqlite3.IntegrityError as exc:
                            raise ValueError("duplicate primary key detected") from exc
                    result["row_count"] = count
                    total_rows += count
                    result["primary_key_fingerprint"] = key_digest.as_dict()
                    result["data_fingerprint"] = data_digest.as_dict()
                    result["aggregate_statistics"] = {
                        key: {"count": numeric_counts[key], "sum": str(value)}
                        for key, value in numeric_sums.items()
                    }
                    if name in expected:
                        wanted = expected[name]
                        differences = []
                        if wanted.get("rows") != count:
                            differences.append(f"rows expected {wanted.get('rows')}, observed {count}")
                        if wanted.get("sha256") and wanted["sha256"].lower() != file_sha:
                            differences.append("file SHA-256 differs from manifest")
                        baseline["manifest_check"]["details"].extend(f"{name}: {item}" for item in differences)
                    elif expected:
                        baseline["manifest_check"]["details"].append(f"{name}: table missing from manifest")
                except (OSError, UnicodeError, csv.Error, ValueError, sqlite3.Error) as exc:
                    result["collection_status"] = "ERROR"
                    result["errors"].append(str(exc))
                    baseline["errors"].append(f"{name}: {exc}")
                    baseline["collection_status"] = "ERROR"
            baseline["total_row_count"] = total_rows
        details = baseline["manifest_check"]["details"]
        if details:
            baseline["manifest_check"]["status"] = "MISMATCH"
        elif expected:
            baseline["manifest_check"]["status"] = "PASS"
        if baseline["errors"]:
            baseline["collection_status"] = "ERROR"
    finally:
        Path(temp_name).unlink(missing_ok=True)
    import resource
    baseline["collection_metrics"] = {
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "peak_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024),
        "memory_metric": "process peak RSS from getrusage; Linux reports KiB",
    }
    return baseline
