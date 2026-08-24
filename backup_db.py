"""SQLite backup via backup API (online, safe while server runs).
Usage: python backup_db.py [output_dir]  — creates backups/meblio-YYYYMMDD-HHMMSS.db
"""
import pathlib
import sqlite3
import sys
import time

BASE_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = pathlib.Path(str(BASE_DIR / "meblio.db"))


def backup(output_dir=None):
    out_dir = pathlib.Path(output_dir) if output_dir else BASE_DIR / "backups"
    out_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = out_dir / f"meblio-{stamp}.db"
    src = sqlite3.connect(DB_PATH)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    print(f"backup saved: {dest} ({dest.stat().st_size} bytes)")
    return dest


if __name__ == "__main__":
    backup(sys.argv[1] if len(sys.argv) > 1 else None)