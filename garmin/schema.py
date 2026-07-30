"""SQLite schema for the raw Garmin pull.

Every table keeps the full raw API response as JSON alongside a handful of
extracted scalar columns, so early analysis doesn't have to reach back into
the API to get a field nobody thought to pull out up front.
"""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "running.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_metrics (
    date                TEXT PRIMARY KEY,   -- ISO date, e.g. 2026-07-28
    training_readiness  INTEGER,            -- score, 0-100
    hrv_status          TEXT,               -- BALANCED / UNBALANCED / LOW / ...
    hrv_last_night_avg  REAL,
    resting_hr          INTEGER,
    sleep_seconds       INTEGER,
    training_status     TEXT,               -- PRODUCTIVE / MAINTAINING / ...
    training_readiness_raw TEXT,            -- full JSON response
    hrv_raw             TEXT,
    rhr_raw             TEXT,
    sleep_raw           TEXT,
    training_status_raw TEXT,
    fetched_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS race_predictions (
    date                TEXT NOT NULL,
    distance            TEXT NOT NULL,      -- 5k / 10k / half_marathon / marathon
    predicted_seconds   REAL,
    fetched_at          TEXT NOT NULL,
    PRIMARY KEY (date, distance)
);

CREATE TABLE IF NOT EXISTS activities (
    activity_id         TEXT PRIMARY KEY,
    date                TEXT NOT NULL,
    activity_type       TEXT,
    duration_s          REAL,
    distance_m          REAL,
    avg_hr              REAL,
    avg_speed_mps       REAL,
    summary_raw         TEXT,               -- full activity summary JSON
    weather_raw         TEXT,
    hr_timezones_raw    TEXT,
    splits_raw          TEXT,
    fetched_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(date);
"""


def init_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
