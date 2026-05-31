from __future__ import annotations

import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "scene1.sqlite3"


def _dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        for statement in SCHEMA:
            conn.execute(statement)
        conn.commit()


def table_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()
    return int(row["total"])


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS zones (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        scene_type TEXT NOT NULL,
        capacity INTEGER NOT NULL,
        area_m2 REAL NOT NULL,
        speed_limit REAL NOT NULL,
        description TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sensors (
        id TEXT PRIMARY KEY,
        zone_id TEXT NOT NULL,
        name TEXT NOT NULL,
        sensor_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'online',
        last_seen TEXT,
        FOREIGN KEY(zone_id) REFERENCES zones(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS watchlist (
        id TEXT PRIMARY KEY,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        category TEXT NOT NULL,
        severity TEXT NOT NULL,
        risk_score INTEGER NOT NULL,
        description TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        event_type TEXT NOT NULL,
        zone_id TEXT NOT NULL,
        sensor_id TEXT,
        subject_type TEXT,
        subject_id TEXT,
        plate_no TEXT,
        device_id TEXT,
        direction TEXT,
        speed_kmh REAL,
        confidence REAL NOT NULL DEFAULT 0.85,
        metadata TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(zone_id) REFERENCES zones(id),
        FOREIGN KEY(sensor_id) REFERENCES sensors(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alerts (
        id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        severity TEXT NOT NULL,
        rule_code TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        zone_id TEXT NOT NULL,
        event_id TEXT,
        target_type TEXT,
        target_id TEXT,
        score INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        evidence TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(zone_id) REFERENCES zones(id),
        FOREIGN KEY(event_id) REFERENCES events(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_zone_ts ON events(zone_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_subject ON events(subject_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_plate ON events(plate_no, ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_device ON events(device_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_status_ts ON alerts(status, ts)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_rule ON alerts(rule_code, ts)",
]
