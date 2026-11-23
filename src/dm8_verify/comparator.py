"""Evidence-aware baseline comparison."""
from __future__ import annotations

from typing import Any


def _item(status: str, before: Any = None, after: Any = None, detail: str | None = None) -> dict[str, Any]:
    result = {"status": status, "before": before, "after": after}
    if detail:
        result["detail"] = detail
    return result


VALIDATION_LEVELS = {"ROW_COUNT", "DATA_ONLY", "FULL"}


def compare_baselines(before: dict[str, Any], after: dict[str, Any], level: str = "FULL") -> dict[str, Any]:
    level = level.upper()
    if level not in VALIDATION_LEVELS:
        raise ValueError(f"unsupported validation level: {level}")
    differences: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    errors: list[str] = []
    if before.get("schema_version") != after.get("schema_version"):
        differences.append(_item("MISMATCH", before.get("schema_version"), after.get("schema_version"), "schema version differs"))
    if before.get("fingerprint_version") != after.get("fingerprint_version"):
        incomplete.append(_item("INCOMPLETE", before.get("fingerprint_version"), after.get("fingerprint_version"), "fingerprint version is incompatible"))
    source_pair = (before.get("source_type"), after.get("source_type"))
    allowed = source_pair in {("SOURCE", "SOURCE"), ("DATABASE", "RESTORED"), ("RESTORED", "DATABASE"), ("DATABASE", "DATABASE"), ("RESTORED", "RESTORED"), ("FIXTURE", "FIXTURE")}
    cross_source = source_pair[0] != source_pair[1]
    tables_before = before.get("tables") if isinstance(before.get("tables"), dict) else {}
    tables_after = after.get("tables") if isinstance(after.get("tables"), dict) else {}
    table_results: dict[str, Any] = {}
    for name in sorted(set(tables_before) | set(tables_after)):
        left, right = tables_before.get(name), tables_after.get(name)
        if left is None or right is None:
            if level == "ROW_COUNT":
                table_results[name] = {"row_count": _item("INCOMPLETE", left, right, "table/count evidence missing in one baseline")}
                incomplete.append({"table": name, "check": "row_count"})
            else:
                table_results[name] = _item("MISMATCH", left, right, "table missing in one baseline")
                differences.append(table_results[name])
            continue
        checks: dict[str, Any] = {}
        checks_to_run = [("row_count", "row_count")]
        if level in {"DATA_ONLY", "FULL"}:
            checks_to_run.extend([("primary_key", "primary_key_fingerprint"), ("data", "data_fingerprint")])
        for label, key in checks_to_run:
            lval, rval = left.get(key), right.get(key)
            if lval is None or rval is None:
                checks[label] = _item("INCOMPLETE", lval, rval, "evidence not collected")
                incomplete.append({"table": name, "check": label})
            elif lval == rval:
                checks[label] = _item("MATCH", lval, rval)
            else:
                checks[label] = _item("MISMATCH", lval, rval)
                differences.append({"table": name, "check": label, "status": "MISMATCH", "before": lval, "after": rval})
        metadata_checks = (("columns", "column_metadata"), ("constraints", "constraint_metadata"), ("indexes", "index_metadata")) if level == "FULL" else ()
        for label, key in metadata_checks:
            lval, rval = left.get(key), right.get(key)
            if lval is None or rval is None or (isinstance(lval, dict) and lval.get("status") in {"NOT_COLLECTED", "UNAVAILABLE"}) or (isinstance(rval, dict) and rval.get("status") in {"NOT_COLLECTED", "UNAVAILABLE"}):
                checks[label] = _item("INCOMPLETE", lval, rval, "metadata not collected")
                incomplete.append({"table": name, "check": label})
            elif lval == rval:
                checks[label] = _item("MATCH", lval, rval)
            else:
                checks[label] = _item("MISMATCH", lval, rval)
                differences.append({"table": name, "check": label, "status": "MISMATCH", "before": lval, "after": rval})
        lval, rval = left.get("aggregate_statistics"), right.get("aggregate_statistics")
        if level != "FULL":
            lval = rval = {"status": "SKIPPED", "level": level}
        if lval is None or rval is None or (isinstance(lval, dict) and lval.get("status") in {"NOT_COLLECTED", "UNAVAILABLE"}) or (isinstance(rval, dict) and rval.get("status") in {"NOT_COLLECTED", "UNAVAILABLE"}):
            checks["aggregates"] = _item("INCOMPLETE", lval, rval, "aggregate statistics not collected")
            incomplete.append({"table": name, "check": "aggregates"})
        elif lval == rval:
            checks["aggregates"] = _item("MATCH", lval, rval)
        else:
            checks["aggregates"] = _item("MISMATCH", lval, rval)
            differences.append({"table": name, "check": "aggregates", "status": "MISMATCH", "before": lval, "after": rval})
        table_results[name] = checks
    for label, baseline in (("before", before), ("after", after)):
        collected_errors = baseline.get("errors")
        if isinstance(collected_errors, list):
            errors.extend([f"{label}: {error}" for error in collected_errors])
        if baseline.get("collection_status") == "ERROR" and not collected_errors:
            errors.append(f"{label}: collection status is ERROR")
        manifest = baseline.get("manifest_check", {})
        if isinstance(manifest, dict) and manifest.get("status") == "MISMATCH":
            differences.append({"check": "source_manifest", "status": "MISMATCH", "source": label, "details": manifest.get("details", [])})
    if not allowed:
        incomplete.append({"check": "source_compatibility", "status": "INCOMPLETE", "detail": f"comparison {source_pair[0]} -> {source_pair[1]} is cross-source"})
    required_complete = not incomplete and not errors and bool(table_results)
    status = "ERROR" if errors else "PASS" if required_complete and not differences else "FAIL" if differences else "INCOMPLETE"
    if errors:
        status = "ERROR"
    return {
        "schema_version": "1.0", "comparison_version": "1.0", "validation_level": level,
        "before_source_type": before.get("source_type"), "after_source_type": after.get("source_type"),
        "cross_source": cross_source, "status": status,
        "table_results": table_results, "differences": differences, "incomplete": incomplete, "errors": errors,
        "summary": {"before_total_row_count": before.get("total_row_count"), "after_total_row_count": after.get("total_row_count"), "table_count": len(table_results)},
    }
