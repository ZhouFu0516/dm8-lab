#!/usr/bin/env python3
"""Copy one file between libvirt guests using QEMU Guest Agent file RPCs."""
import argparse
import base64
import json
import subprocess
import sys


def rpc(domain: str, execute: str, arguments: dict) -> dict:
    request = json.dumps({"execute": execute, "arguments": arguments}, separators=(",", ":"))
    result = subprocess.run(
        ["virsh", "--connect", "qemu:///system", "qemu-agent-command", domain, request],
        check=True, capture_output=True, text=True,
    )
    response = json.loads(result.stdout)
    if "error" in response:
        raise RuntimeError(f"QGA {execute} failed on {domain}: {response['error'].get('desc', 'unknown error')}")
    return response.get("return", {})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_domain")
    parser.add_argument("source_path")
    parser.add_argument("target_domain")
    parser.add_argument("target_path")
    parser.add_argument("--chunk-size", type=int, default=64 * 1024)
    args = parser.parse_args()
    if args.chunk_size < 4096 or args.chunk_size > 64 * 1024:
        parser.error("--chunk-size must be between 4096 and 65536 bytes")

    source = rpc(args.source_domain, "guest-file-open", {"path": args.source_path, "mode": "r"})
    target = rpc(args.target_domain, "guest-file-open", {"path": args.target_path, "mode": "w"})
    total = 0
    try:
        while True:
            block = rpc(args.source_domain, "guest-file-read", {"handle": source, "count": args.chunk_size})
            encoded = block.get("buf-b64", "")
            if encoded:
                data = base64.b64decode(encoded, validate=True)
                write_result = rpc(args.target_domain, "guest-file-write", {
                    "handle": target,
                    "buf-b64": base64.b64encode(data).decode("ascii"),
                    "count": len(data),
                })
                written = write_result.get("count", 0)
                if written != len(data):
                    raise RuntimeError(f"short write at offset {total}: {written}/{len(data)}")
                total += written
            if block.get("eof"):
                break
    finally:
        rpc(args.source_domain, "guest-file-close", {"handle": source})
        rpc(args.target_domain, "guest-file-close", {"handle": target})
    print(f"copied_bytes={total}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"copy failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
