"""Versioned canonical encoding and order-independent multiset fingerprints."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Iterable, Sequence

FINGERPRINT_VERSION = "dm8-multiset-sha256-v1"


def normalize(value: str | None, column: dict[str, Any]) -> Any:
    if value is None or value == r"\N":
        if not column.get("nullable", True):
            raise ValueError(f"{column['name']} is NULL but declared NOT NULL")
        return None
    kind = column["type"].upper()
    if kind in {"INT", "INTEGER", "BIGINT"}:
        if value.strip() == "":
            raise ValueError(f"empty numeric value in {column['name']}")
        return str(int(value, 10))
    if kind == "DATE":
        return date.fromisoformat(value).isoformat()
    if kind in {"DECIMAL", "NUMERIC"}:
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"invalid decimal in {column['name']}: {value!r}") from exc
        scale = int(column.get("scale", 0))
        normalized = number.quantize(Decimal(1).scaleb(-scale))
        precision = column.get("precision")
        if precision is not None and len(normalized.copy_abs().quantize(Decimal(1).scaleb(-scale)).as_tuple().digits) > int(precision):
            raise ValueError(f"decimal precision overflow in {column['name']}")
        return format(normalized, f".{scale}f")
    if "length" in column and len(value) > int(column["length"]):
        raise ValueError(f"value exceeds declared length for {column['name']}")
    return value


def canonical_bytes(values: Sequence[Any]) -> bytes:
    """Type-tag values and JSON-escape strings; NULL differs from empty text."""
    tagged = [["null"] if value is None else ["value", str(value)] for value in values]
    return json.dumps(tagged, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class MultisetDigest:
    """Order-independent SHA-256 multiset accumulator using count, sum and xor."""

    def __init__(self, domain: str) -> None:
        self.domain = domain.encode("utf-8")
        self.count = 0
        self.total = 0
        self.xor = 0

    def add(self, payload: bytes) -> None:
        digest = hashlib.sha256(self.domain + b"\0" + payload).digest()
        integer = int.from_bytes(digest, "big")
        self.count += 1
        self.total = (self.total + integer) % (1 << 256)
        self.xor ^= integer

    def as_dict(self) -> dict[str, Any]:
        material = f"{FINGERPRINT_VERSION}:{self.domain.decode()}:{self.count}:{self.total:064x}:{self.xor:064x}"
        return {
            "algorithm": FINGERPRINT_VERSION,
            "count": self.count,
            "sum_sha256": f"{self.total:064x}",
            "xor_sha256": f"{self.xor:064x}",
            "sha256": hashlib.sha256(material.encode()).hexdigest(),
        }


def fingerprint_rows(rows: Iterable[bytes], domain: str) -> dict[str, Any]:
    digest = MultisetDigest(domain)
    for row in rows:
        digest.add(row)
    return digest.as_dict()
