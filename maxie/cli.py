"""`maxie` command line: backups. (Google accounts are connected from Settings in the web app.)

    maxie backup [--verify] [--force] [--reason TEXT]   make an encrypted backup (then test-restore it)
    maxie backup-status                                  what the panel shows
    maxie restore <backup-name> <folder>                 decrypt one into a folder
    maxie warm-report                                    the warm-Sonnet experiment's numbers
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .backup import Backup, default_dirs, default_sources, status_dict
from .config import data_dir, secret


def make_backup() -> Backup:
    return Backup(default_dirs(), default_sources(data_dir()), secret("BACKUP_PASSPHRASE"))


def restore_target(arg: str) -> tuple[str, Path | None]:
    """`restore <name>` or `restore <path/to/name.tar.gz.age.001>` (any part): (name, its folder or None)."""
    p = Path(arg).expanduser()
    if p.suffix and p.suffix[1:].isdigit() and p.parent.exists():
        return p.name.rsplit(".", 1)[0], p.parent
    return arg, None


def warm_report(db_path: Path | None = None) -> str:
    """Reads the hub's database (counts and timings only, no message text) and prints the experiment table."""
    from .config import Settings
    from .db import DB
    from .warm_experiment import WarmExperiment
    return WarmExperiment(DB(db_path or data_dir() / "hub.db"), Settings.load()).markdown()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="maxie")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--verify", action="store_true", help="restore the newest backup into a temp dir and open its databases")
    b.add_argument("--force", action="store_true", help="even if nothing changed")
    b.add_argument("--reason", default="manual")
    sub.add_parser("backup-status")
    r = sub.add_parser("restore")
    r.add_argument("name", help="a backup name from `backup-status`, or the path of one of its .age.NNN files")
    r.add_argument("folder", nargs="?", help="where to put the files (default: ~/maxie-restore)")
    sub.add_parser("warm-report", help="the warm-Sonnet experiment's numbers and verdict, as markdown")
    args = ap.parse_args(argv)
    if args.cmd == "warm-report":
        print(warm_report())
        return 0
    bk = make_backup()
    if args.cmd == "backup":
        bk.run(args.reason, force=args.force)
        ok = bk.status().last_ok
        if args.verify:
            ok = bk.verify_latest() and ok
        return 0 if ok else 1
    if args.cmd == "backup-status":
        print(json.dumps(status_dict(bk), indent=1))
        return 0
    name, where = restore_target(args.name)
    folder = Path(args.folder or "~/maxie-restore").expanduser()
    dbs = bk.restore(name, folder, where)
    print("restored:", *[str(d) for d in dbs], sep="\n  ")
    print(f"read {folder}/RESTORE.md for the secrets to re-add")
    return 0


if __name__ == "__main__":
    sys.exit(main())
