from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from argparse import Namespace

from dm8_verify.collectors import dm8 as dm8_collector
from dm8_verify.collectors.dm8 import build_disql_script, parse_disql_output, stream_disql_output
from dm8_verify.collectors.source import collect_source, load_config
from dm8_verify.comparator import compare_baselines
from dm8_verify.cli import EXIT_DIFF, EXIT_OK, command_compare
from dm8_verify.fingerprint import MultisetDigest, canonical_bytes


class FingerprintTests(unittest.TestCase):
    def test_multiset_is_order_independent(self):
        left, right = MultisetDigest("test"), MultisetDigest("test")
        for value in (b"a", b"b", b"a"):
            left.add(value)
        for value in (b"a", b"a", b"b"):
            right.add(value)
        self.assertEqual(left.as_dict(), right.as_dict())

    def test_mutation_changes_fingerprint(self):
        left, right = MultisetDigest("test"), MultisetDigest("test")
        left.add(canonical_bytes(["1", "alpha"]))
        right.add(canonical_bytes(["1", "beta"]))
        self.assertNotEqual(left.as_dict(), right.as_dict())

    def test_null_and_empty_are_distinct(self):
        self.assertNotEqual(canonical_bytes([None]), canonical_bytes([""]))


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.config = self.root / "tables.json"
        self.manifest = self.root / "manifest.json"
        self._write_config()
        self.write_rows(["1|a|2024-01-01|1.20", "2|b|2024-01-02|3.40"])
        self.manifest.write_text(json.dumps({"tables": [{"table": "ITEMS", "rows": 2, "sha256": self.file_sha()}]}))

    def tearDown(self):
        self.temp.cleanup()

    def _write_config(self, constraints=None):
        value = {"schema_name": "T", "tables": [{
            "name": "ITEMS", "file": "items.txt", "columns": [
                {"name": "ID", "type": "INT", "nullable": False},
                {"name": "NAME", "type": "VARCHAR", "length": 20, "nullable": False},
                {"name": "CREATED", "type": "DATE", "nullable": False},
                {"name": "AMOUNT", "type": "DECIMAL", "precision": 8, "scale": 2, "nullable": False},
            ], "primary_key": ["ID"], "constraints": constraints or ["PK(ID)"]
        }]}
        self.config.write_text(json.dumps(value))

    def write_rows(self, lines):
        (self.data / "items.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def file_sha(self):
        import hashlib
        return hashlib.sha256((self.data / "items.txt").read_bytes()).hexdigest()

    def collect(self):
        return collect_source(self.data, self.config, self.manifest)

    def test_collects_rows_and_manifest(self):
        baseline = self.collect()
        self.assertEqual(baseline["tables"]["ITEMS"]["row_count"], 2)
        self.assertEqual(baseline["total_row_count"], 2)
        self.assertEqual(baseline["manifest_check"]["status"], "PASS")
        self.assertEqual(baseline["source_type"], "SOURCE")

    def test_same_rows_different_order_have_same_digest(self):
        before = self.collect()
        self.write_rows(["2|b|2024-01-02|3.40", "1|a|2024-01-01|1.20"])
        after = self.collect()
        self.assertEqual(before["tables"]["ITEMS"]["data_fingerprint"], after["tables"]["ITEMS"]["data_fingerprint"])
        self.assertEqual(before["tables"]["ITEMS"]["primary_key_fingerprint"], after["tables"]["ITEMS"]["primary_key_fingerprint"])

    def test_single_field_change_same_count_detected(self):
        before = self.collect()
        self.write_rows(["1|changed|2024-01-01|1.20", "2|b|2024-01-02|3.40"])
        after = self.collect()
        self.assertEqual(before["tables"]["ITEMS"]["row_count"], after["tables"]["ITEMS"]["row_count"])
        self.assertNotEqual(before["tables"]["ITEMS"]["data_fingerprint"], after["tables"]["ITEMS"]["data_fingerprint"])

    def test_delete_row_detected(self):
        before = self.collect()
        self.write_rows(["1|a|2024-01-01|1.20"])
        after = self.collect()
        compared = compare_baselines(before, after)
        self.assertEqual(compared["status"], "FAIL")
        self.assertEqual(after["tables"]["ITEMS"]["row_count"], 1)

    def test_primary_key_change_detected(self):
        before = self.collect()
        self.write_rows(["1|a|2024-01-01|1.20", "3|b|2024-01-02|3.40"])
        after = self.collect()
        self.assertNotEqual(before["tables"]["ITEMS"]["primary_key_fingerprint"], after["tables"]["ITEMS"]["primary_key_fingerprint"])

    def test_duplicate_primary_key_is_error(self):
        self.write_rows(["1|a|2024-01-01|1.20", "1|b|2024-01-02|3.40"])
        result = self.collect()
        self.assertEqual(result["collection_status"], "ERROR")
        self.assertIn("duplicate primary key", " ".join(result["errors"]))

    def test_missing_file_is_error(self):
        (self.data / "items.txt").unlink()
        result = self.collect()
        self.assertEqual(result["collection_status"], "ERROR")
        self.assertIsNone(result["tables"]["ITEMS"]["row_count"])

    def test_field_count_error(self):
        self.write_rows(["1|too-few"])
        result = self.collect()
        self.assertEqual(result["collection_status"], "ERROR")

    def test_manifest_discrepancy_reported(self):
        self.manifest.write_text(json.dumps({"tables": [{"table": "ITEMS", "rows": 99}]}))
        result = self.collect()
        self.assertEqual(result["manifest_check"]["status"], "MISMATCH")

    def test_invalid_config_rejected(self):
        self.config.write_text("{}")
        with self.assertRaises(ValueError):
            load_config(self.config)

    def test_constraint_change_is_difference(self):
        before = self.collect()
        self._write_config(["PK(ID)", "CHECK(AMOUNT >= 0)"])
        after = self.collect()
        self.assertEqual(compare_baselines(before, after)["status"], "FAIL")

    def test_missing_evidence_is_incomplete(self):
        before = self.collect()
        after = self.collect()
        after["tables"]["ITEMS"]["data_fingerprint"] = None
        result = compare_baselines(before, after)
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertTrue(any(item.get("check") == "data" for item in result["incomplete"]))


class ComparatorTests(unittest.TestCase):
    def baseline(self, source="SOURCE"):
        fp = {"algorithm": "v1", "sha256": "a"}
        metadata = {"status": "SOURCE_DDL", "items": []}
        return {"schema_version": "1", "fingerprint_version": "v1", "source_type": source, "total_row_count": 1, "errors": [], "tables": {"T": {
            "row_count": 1, "primary_key_fingerprint": fp, "data_fingerprint": fp,
            "column_metadata": metadata, "constraint_metadata": metadata, "index_metadata": metadata,
            "aggregate_statistics": {}
        }}}

    def test_cross_source_never_passes(self):
        result = compare_baselines(self.baseline("SOURCE"), self.baseline("DATABASE"))
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertTrue(result["cross_source"])

    def test_fingerprint_version_mismatch_incomplete(self):
        right = self.baseline()
        right["fingerprint_version"] = "v2"
        self.assertEqual(compare_baselines(self.baseline(), right)["status"], "INCOMPLETE")

    def test_missing_table_fails(self):
        right = self.baseline()
        right["tables"] = {}
        self.assertEqual(compare_baselines(self.baseline(), right)["status"], "FAIL")

    def test_database_metadata_not_collected_incomplete(self):
        left, right = self.baseline("DATABASE"), self.baseline("RESTORED")
        right["tables"]["T"]["index_metadata"] = {"status": "NOT_COLLECTED", "items": None}
        self.assertEqual(compare_baselines(left, right)["status"], "INCOMPLETE")

    def test_aggregate_change_is_difference(self):
        left, right = self.baseline(), self.baseline()
        left["tables"]["T"]["aggregate_statistics"] = {"A": {"sum": "1"}}
        right["tables"]["T"]["aggregate_statistics"] = {"A": {"sum": "2"}}
        self.assertEqual(compare_baselines(left, right)["status"], "FAIL")

    def test_cli_compare_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before, after = self.baseline(), self.baseline()
            before_path, after_path = root / "before.json", root / "after.json"
            before_path.write_text(json.dumps(before), encoding="utf-8")
            after["tables"]["T"]["row_count"] = 2
            after_path.write_text(json.dumps(after), encoding="utf-8")
            self.assertEqual(command_compare(Namespace(before=before_path, after=after_path, output=root / "comparison")), EXIT_DIFF)
            after["tables"]["T"]["row_count"] = 1
            after_path.write_text(json.dumps(after), encoding="utf-8")
            self.assertEqual(command_compare(Namespace(before=before_path, after=after_path, output=root / "same")), EXIT_OK)


class DisqlTests(unittest.TestCase):
    def test_parser_handles_headers_and_markers(self):
        output = "DM8 banner\n__DM8_VERIFY_BEGIN__T\n1\talpha\n__DM8_VERIFY_END__T\n"
        self.assertEqual(parse_disql_output(output, ["T"]), {"T": [["1", "alpha"]]})

    def test_parser_rejects_missing_section(self):
        with self.assertRaises(ValueError):
            parse_disql_output("banner\n", ["T"])

    def test_missing_database_client_is_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "failed to execute disql"):
            list(stream_disql_output({"disql_path": "/not/a/real/disql"}, "EXIT\n", ["T"]))

    def test_script_is_select_only_and_password_not_argv(self):
        import os
        old = os.environ.get("DM8_VERIFY_PASSWORD")
        os.environ["DM8_VERIFY_PASSWORD"] = "not-a-real-secret"
        try:
            tables = [{"name": "T", "columns": [{"name": "ID", "type": "INT"}], "primary_key": ["ID"]}]
            script = build_disql_script({"username": "readonly", "host": "127.0.0.1", "service": "DM", "password_env": "DM8_VERIFY_PASSWORD"}, tables)
            self.assertIn("SELECT", script)
            self.assertIn("EXIT", script)
            self.assertNotIn("INSERT", script.upper())
            self.assertIn("not-a-real-secret", script)
        finally:
            if old is None:
                os.environ.pop("DM8_VERIFY_PASSWORD", None)
            else:
                os.environ["DM8_VERIFY_PASSWORD"] = old

    def test_online_collector_labels_restored_and_rejects_duplicate_keys(self):
        import os
        from unittest.mock import patch
        os.environ["DM8_VERIFY_PASSWORD"] = "test-password"
        table = {"name": "T", "columns": [{"name": "ID", "type": "INT", "nullable": False}], "primary_key": ["ID"]}
        config = {"username": "readonly", "host": "127.0.0.1", "service": "DM"}
        try:
            with patch.object(dm8_collector, "stream_disql_output", return_value=iter([("T", ["1"]), ("T", ["2"])])):
                result = dm8_collector.collect_dm8(config, [table], source_type="RESTORED")
                self.assertEqual(result["source_type"], "RESTORED")
            with patch.object(dm8_collector, "stream_disql_output", return_value=iter([("T", ["1"]), ("T", ["1"])])):
                with self.assertRaisesRegex(RuntimeError, "duplicate primary key"):
                    dm8_collector.collect_dm8(config, [table])
        finally:
            os.environ.pop("DM8_VERIFY_PASSWORD", None)


if __name__ == "__main__":
    unittest.main()
