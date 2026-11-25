from __future__ import annotations

import argparse

from . import backup, integration, restore


def main() -> int:
    parser = argparse.ArgumentParser(prog="dm8_mvp")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("restore", help="run the isolated physical restore MVP")
    r.add_argument("--config", required=True)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--execute", action="store_true")
    b = sub.add_parser("backup", help="construct the fixed-environment B1 backup flow")
    b.add_argument("--config", required=True)
    b.add_argument("--dry-run", action="store_true")
    i = sub.add_parser("run", help="construct or explicitly execute the I1 integration flow")
    i.add_argument("--config", required=True)
    i.add_argument("--dry-run", action="store_true")
    i.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.command == "restore":
        argv = ["--config", args.config]
        if args.dry_run:
            argv.append("--dry-run")
        if args.execute:
            argv.append("--execute")
        return restore.main(argv)
    if args.command == "backup":
        argv = ["--config", args.config]
        if args.dry_run:
            argv.append("--dry-run")
        return backup.main(argv)
    if args.command == "run":
        argv = ["--config", args.config]
        if args.dry_run:
            argv.append("--dry-run")
        if args.execute:
            argv.append("--execute")
        return integration.main(argv)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

