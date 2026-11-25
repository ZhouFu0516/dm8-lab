"""JSON, CSV and Markdown output writers."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_baseline_outputs(baseline: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "baseline.json", baseline)
    with (output_dir / "tables.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["table_name", "row_count", "collection_status", "primary_key_sha256", "data_sha256"])
        for name, table in baseline.get("tables", {}).items():
            writer.writerow([name, table.get("row_count"), table.get("collection_status"), (table.get("primary_key_fingerprint") or {}).get("sha256"), (table.get("data_fingerprint") or {}).get("sha256")])


def comparison_markdown(comparison: dict[str, Any]) -> str:
    if comparison.get("validation_level") == "ROW_COUNT":
        lines = [
            "# DM8 行数比较报告", "", f"- 总体状态：`{comparison.get('status')}`",
            "- 验证等级：`ROW_COUNT`",
            f"- 来源：`{comparison.get('before_source_type')}` → `{comparison.get('after_source_type')}`",
            "- 证据边界：仅比较逐表记录数，不代表数据内容一致。", "",
            "| 表 | 比较状态 | 前次行数 | 本次行数 | 差值 |", "|---|---|---:|---:|---:|",
        ]
        for name, checks in comparison.get("table_results", {}).items():
            result = checks.get("row_count", {})
            before, after = result.get("before"), result.get("after")
            difference = after - before if isinstance(before, int) and isinstance(after, int) else "—"
            lines.append(f"| {name} | {result.get('status')} | {before if before is not None else '—'} | {after if after is not None else '—'} | {difference} |")
        lines.extend(["", f"差异数：{len(comparison.get('differences', []))}", f"证据不足项：{len(comparison.get('incomplete', []))}", f"错误数：{len(comparison.get('errors', []))}"])
        return "\n".join(lines) + "\n"
    lines = [
        "# DM8 一致性比较报告", "", f"- 总体状态：`{comparison.get('status')}`", f"- 验证等级：`{comparison.get('validation_level', 'FULL')}`",
        f"- 来源：`{comparison.get('before_source_type')}` → `{comparison.get('after_source_type')}`",
        f"- 跨来源：`{comparison.get('cross_source')}`", "", "| 表 | 行数 | 主键摘要 | 数据摘要 | 结构 |", "|---|---|---|---|---|",
    ]
    for name, checks in comparison.get("table_results", {}).items():
        lines.append(f"| {name} | {checks.get('row_count', {}).get('status')} | {checks.get('primary_key', {}).get('status')} | {checks.get('data', {}).get('status')} | {checks.get('columns', {}).get('status')} |")
    lines.extend(["", f"差异数：{len(comparison.get('differences', []))}", f"证据不足项：{len(comparison.get('incomplete', []))}", f"错误数：{len(comparison.get('errors', []))}"])
    if comparison.get("differences"):
        lines.extend(["", "## 差异", ""] + [f"- `{item}`" for item in comparison["differences"]])
    if comparison.get("incomplete"):
        lines.extend(["", "## 未完成证据", ""] + [f"- `{item}`" for item in comparison["incomplete"]])
    return "\n".join(lines) + "\n"


def write_comparison_outputs(comparison: dict[str, Any], output_prefix: Path) -> None:
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_prefix.with_suffix(".json"), comparison)
    output_prefix.with_suffix(".md").write_text(comparison_markdown(comparison), encoding="utf-8")
    with output_prefix.with_suffix(".csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        if comparison.get("validation_level") == "ROW_COUNT":
            writer.writerow(["table_name", "before_count", "after_count", "difference", "status"])
            for table, checks in comparison.get("table_results", {}).items():
                result = checks.get("row_count", {})
                before = result.get("before")
                after = result.get("after")
                difference = after - before if isinstance(before, int) and isinstance(after, int) else ""
                writer.writerow([table, before, after, difference, "MATCH" if result.get("status") == "MATCH" else result.get("status")])
            return
        writer.writerow(["table", "check", "status", "detail"])
        for table, checks in comparison.get("table_results", {}).items():
            for check, result in checks.items():
                writer.writerow([table, check, result.get("status"), result.get("detail", "")])
