"""Upload completed local sessions that remain in the SQLite outbox.

Run from the cable-analyzer directory:
    python -m scripts.sync_remote
"""
# new
from __future__ import annotations

import argparse
import tomllib

from src.telemetry.remote import sync_pending
from src.telemetry.storage import Storage


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload pending telemetry to Supabase")
    parser.add_argument("--config", default="config.toml")
    args = parser.parse_args()
    with open(args.config, "rb") as fh:
        cfg = tomllib.load(fh)
    storage = Storage(cfg.get("paths", {}).get("data_dir", "data"))
    try:
        result = sync_pending(storage, cfg)
        print(
            "remote sync: "
            f"{result['completed']} completed, "
            f"{result['failed']} failed, "
            f"{result['pending']} pending"
        )
        if result["failed"]:
            errors = storage.conn.execute(
                """SELECT session_id, last_error FROM remote_queue
                   WHERE status='pending' AND last_error IS NOT NULL
                   ORDER BY rowid"""
            ).fetchall()
            for row in errors:
                print(f"  error [{row['session_id']}]: {row['last_error']}")
        if (
            bool(cfg.get("remote", {}).get("enabled", False))
            and result["attempted"] == 0
            and result["pending"]
            and not result["configured"]
        ):
            print(
                "  Set BASE and CABLE_INGEST_TOKEN in .env before retrying."
            )
            return 2
        return 0 if result["failed"] == 0 else 1
    finally:
        storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
