"""Command-line entry point."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

from .collectors.dm8 import collect_dm8
from .collectors.source import collect_source, load_config
from .comparator import compare_baselines
from .reporter import write_baseline_outputs, write_comparison_outputs, write_json

EXIT_OK = 0
EXIT_DIFF = 1
EXIT_INPUT = 2
EXIT_INCOMPLETE = 3


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_new_baseline(input_dir: Path, config: Path, manifest: Path | None, output: Path) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}; choose a new path")
    baseline = collect_source(input_dir, config, manifest)
    write_baseline_outputs(baseline, output)
    return baseline


def _status_exit(status: str) -> int:
    return {"PASS": EXIT_OK, "FAIL": EXIT_DIFF, "ERROR": EXIT_INPUT, "INCOMPLETE": EXIT_INCOMPLETE}.get(status, EXIT_INPUT)


def command_collect_source(args: argparse.Namespace) -> int:
    try:
        baseline = _write_new_baseline(args.input, args.config, args.manifest, args.output)
    except (OSError, ValueError) as exc:
        print(f"collect-source error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    print(json.dumps({"status": baseline["collection_status"], "total_row_count": baseline.get("total_row_count"), "output": str(args.output)}, ensure_ascii=False))
    return EXIT_OK if baseline["collection_status"] == "PASS" and baseline.get("manifest_check", {}).get("status") in {"PASS", "NOT_COLLECTED"} else EXIT_INPUT


def command_compare(args: argparse.Namespace) -> int:
    try:
        before, after = json.loads(args.before.read_text(encoding="utf-8")), json.loads(args.after.read_text(encoding="utf-8"))
        comparison = compare_baselines(before, after)
        write_comparison_outputs(comparison, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"compare error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    print(json.dumps({"status": comparison["status"], "output": str(args.output)}, ensure_ascii=False))
    return _status_exit(comparison["status"])


def command_collect_db(args: argparse.Namespace) -> int:
    try:
        config = json.loads(args.db_config.read_text(encoding="utf-8"))
        tables = load_config(args.tables_config)["tables"]
        baseline = collect_dm8(config, tables)
        if args.output.exists() and any(args.output.iterdir()):
            raise ValueError(f"output directory is not empty: {args.output}")
        write_baseline_outputs(baseline, args.output)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"collect-db error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    print(json.dumps({"status": baseline["collection_status"], "output": str(args.output)}, ensure_ascii=False))
    return EXIT_OK


def command_demo(args: argparse.Namespace) -> int:
    root = args.root
    output = args.output
    if output.exists() and any(output.iterdir()):
        print(f"demo error: output directory is not empty: {output}", file=sys.stderr)
        return EXIT_INPUT
    output.mkdir(parents=True, exist_ok=True)
    asset_root = root.parent if (root.parent / "fixtures/hr/data").is_dir() else root
    input_dir, config, manifest = asset_root / "fixtures/hr/data", root / "config/tables.json", asset_root / "fixtures/hr/source_manifest.json"
    started = time.perf_counter()
    try:
        before = _write_new_baseline(input_dir, config, manifest, output / "source-before")
        after = _write_new_baseline(input_dir, config, manifest, output / "source-after")
        normal = compare_baselines(before, after)
        write_comparison_outputs(normal, output / "normal-comparison")

        changed = deepcopy(after)
        changed["tables"]["EMPLOYEES"]["row_count"] += 1
        changed["tables"]["EMPLOYEES"]["data_fingerprint"]["sha256"] = "0" * 64
        changed["tables"]["EMPLOYEES"]["primary_key_fingerprint"]["sha256"] = "1" * 64
        anomaly = compare_baselines(before, changed)
        write_comparison_outputs(anomaly, output / "anomaly-comparison")

        incomplete_input = deepcopy(after)
        incomplete_input["tables"]["EMPLOYEES"]["data_fingerprint"] = None
        incomplete = compare_baselines(before, incomplete_input)
        write_comparison_outputs(incomplete, output / "incomplete-comparison")
        summary = {
            "demo": "SOURCE only; no DM8 database was contacted",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "total_row_count": before["total_row_count"],
            "table_row_counts": {name: table["row_count"] for name, table in before["tables"].items()},
            "normal_status": normal["status"], "anomaly_status": anomaly["status"], "incomplete_status": incomplete["status"],
            "online_status": "ONLINE_NOT_VERIFIED",
        }
        write_json(output / "demo-summary.json", summary)
    except (OSError, ValueError) as exc:
        print(f"demo error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    print(json.dumps(summary, ensure_ascii=False))
    return EXIT_OK if normal["status"] == "PASS" and anomaly["status"] == "FAIL" and incomplete["status"] == "INCOMPLETE" else EXIT_DIFF


def build_parser() -> argparse.ArgumentParser:
    root = _default_root()
    parser = argparse.ArgumentParser(prog="dm8-verify", description="DM8 数据基线与一致性校验工具")
    sub = parser.add_subparsers(dest="command", required=True)
    source = sub.add_parser("collect-source", help="流式采集本地 SOURCE 文件")
    source.add_argument("--input", type=Path, required=True)
    source.add_argument("--config", type=Path, default=root / "config/tables.json")
    source.add_argument("--manifest", type=Path)
    source.add_argument("--output", type=Path, required=True)
    source.set_defaults(func=command_collect_source)
    db = sub.add_parser("collect-db", help="只读采集 DM8 数据库（需要真实 disql 和配置）")
    db.add_argument("--db-config", type=Path, required=True)
    db.add_argument("--tables-config", type=Path, default=root / "config/tables.json")
    db.add_argument("--output", type=Path, required=True)
    db.set_defaults(func=command_collect_db)
    compare = sub.add_parser("compare", help="比较两个基线 JSON")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True, help="输出文件前缀")
    compare.set_defaults(func=command_compare)
    demo = sub.add_parser("demo", help="执行 SOURCE 正常、异常和证据不足演示")
    demo.add_argument("--root", type=Path, default=root)
    demo.add_argument("--output", type=Path, default=root / "outputs/demo")
    demo.set_defaults(func=command_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
