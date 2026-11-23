"""Evidence-aware baseline comparison."""
from __future__ import annotations

from typing import Any


def _item(status: str, before: Any = None, after: Any = None, detail: str | None = None) -> dict[str, Any]:
    result = {"status": status, "before": before, "after": after}
    if detail:
        result["detail"] = detail
    return result


def compare_baselines(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
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
            table_results[name] = _item("MISMATCH", left, right, "table missing in one baseline")
            differences.append(table_results[name])
            continue
        checks: dict[str, Any] = {}
        for label, key in (("row_count", "row_count"), ("primary_key", "primary_key_fingerprint"), ("data", "data_fingerprint")):
            lval, rval = left.get(key), right.get(key)
            if lval is None or rval is None:
                checks[label] = _item("INCOMPLETE", lval, rval, "evidence not collected")
                incomplete.append({"table": name, "check": label})
            elif lval == rval:
                checks[label] = _item("MATCH", lval, rval)
            else:
                checks[label] = _item("MISMATCH", lval, rval)
                differences.append({"table": name, "check": label, "status": "MISMATCH", "before": lval, "after": rval})
        for label, key in (("columns", "column_metadata"), ("constraints", "constraint_metadata"), ("indexes", "index_metadata")):
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
    status = "PASS" if required_complete and not differences else "FAIL" if differences else "INCOMPLETE"
    if errors and not differences:
        status = "ERROR"
    return {
        "schema_version": "1.0", "comparison_version": "1.0",
        "before_source_type": before.get("source_type"), "after_source_type": after.get("source_type"),
        "cross_source": cross_source, "status": status,
        "table_results": table_results, "differences": differences, "incomplete": incomplete, "errors": errors,
        "summary": {"before_total_row_count": before.get("total_row_count"), "after_total_row_count": after.get("total_row_count"), "table_count": len(table_results)},
    }
