"""SQLite session storage + CSV export (DEVELOPMENT_PLAN.md §9)."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.telemetry.models import Sample, SessionMeta

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    mode              TEXT,
    v_target          REAL,
    length_m          REAL,
    phone_expected    INTEGER,
    started_at        REAL,
    ended_at          REAL,
    charging_detected INTEGER,
    v_present         INTEGER,
    fault_reason      TEXT,
    probe_json        TEXT,
    verdict_json      TEXT,
    created_at        TEXT
);
CREATE TABLE IF NOT EXISTS samples (
    session_id TEXT,
    t          REAL,
    voltage    REAL,
    current    REAL,
    power      REAL,
    state      TEXT,
    valid      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_samples_session ON samples(session_id);
"""


class Storage:
    def __init__(self, data_dir: str = "data") -> None:
        self.data_dir = Path(data_dir)
        (self.data_dir / "raw").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "processed").mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.data_dir / "sessions.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------ API
    def new_session(self, meta: SessionMeta) -> str:
        if not meta.session_id:
            meta.session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        self.conn.execute(
            """INSERT INTO sessions
               (session_id, mode, v_target, length_m, phone_expected,
                started_at, charging_detected, v_present, fault_reason, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                meta.session_id,
                meta.mode,
                meta.v_target,
                meta.length_m,
                int(meta.phone_expected),
                meta.started_at,
                int(meta.charging_detected),
                int(meta.v_present),
                meta.fault_reason,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()
        return meta.session_id

    def add_samples(self, session_id: str, samples: list[Sample]) -> None:
        rows = [
            (session_id, s.t, s.voltage, s.current, s.power, s.state, int(s.valid))
            for s in samples
        ]
        self.conn.executemany(
            "INSERT INTO samples (session_id, t, voltage, current, power, state, valid) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()

    def save_probe(self, session_id: str, probe: dict) -> None:
        self.conn.execute(
            "UPDATE sessions SET probe_json=? WHERE session_id=?",
            (json.dumps(probe, default=str), session_id),
        )
        self.conn.commit()

    def save_verdict(self, session_id: str, verdict: dict, meta: SessionMeta) -> None:
        self.conn.execute(
            """UPDATE sessions SET verdict_json=?, ended_at=?, charging_detected=?,
               v_present=? WHERE session_id=?""",
            (
                json.dumps(verdict, default=str),
                meta.ended_at,
                int(meta.charging_detected),
                int(meta.v_present),
                session_id,
            ),
        )
        self.conn.commit()

    def export_csv(self, session_id: str, samples: list[Sample]) -> Path:
        path = self.data_dir / "processed" / f"session_{session_id}.csv"
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["t", "voltage_V", "current_A", "power_W", "state", "valid"])
            for s in samples:
                writer.writerow([f"{s.t:.4f}", f"{s.voltage:.4f}", f"{s.current:.4f}",
                                 f"{s.power:.4f}", s.state, int(s.valid)])
        return path

    def list_sessions(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT session_id, mode, created_at, verdict_json FROM sessions "
            "ORDER BY created_at DESC"
        ).fetchall()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
