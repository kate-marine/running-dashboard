"""Personal rolling baselines for HRV and resting HR.

CLAUDE.md's framing: Garmin scores HRV status against a population
(BALANCED / UNBALANCED / LOW). This builds a baseline against *this
runner's own* recent history instead, so "drifting off my baseline" can be
compared against Garmin's population label to see which one actually
carries signal.

Baseline = trailing 30-calendar-day mean/std, `closed="left"` so today's
value is never used to normalize itself (today's z-score only looks
backward -- otherwise a single big swing would partly cancel itself out in
its own baseline, and later using this same z-score to predict same-day
pace would leak information). min_periods=15 so early-block days and
stretches with sparse HRV data don't get a z-score off 2-3 noisy points.

z-score direction is normalized so that positive always means "worse than
usual" for both metrics: HRV below baseline is bad, resting HR above
baseline is bad, so rhr_zscore is sign-flipped from the raw
(value-mean)/std so a reader doesn't have to remember that HRV and RHR point
opposite ways.
"""

import sqlite3

import pandas as pd

from garmin.schema import DEFAULT_DB_PATH

BASELINE_WINDOW = "30D"
MIN_PERIODS = 15


def load_daily_metrics(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT date, hrv_status, hrv_last_night_avg, resting_hr, training_readiness FROM daily_metrics ORDER BY date",
        conn,
    )
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def add_rolling_baseline(df: pd.DataFrame, col: str, invert: bool = False) -> pd.DataFrame:
    roll = df[col].rolling(BASELINE_WINDOW, min_periods=MIN_PERIODS, closed="left")
    mean = roll.mean()
    std = roll.std()
    z = (df[col] - mean) / std
    if invert:
        z = -z
    df[f"{col}_baseline_mean"] = mean
    df[f"{col}_baseline_std"] = std
    df[f"{col}_zscore"] = z
    return df


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(daily_metrics)")}
    new_cols = [
        "hrv_last_night_avg_baseline_mean",
        "hrv_last_night_avg_baseline_std",
        "hrv_last_night_avg_zscore",
        "resting_hr_baseline_mean",
        "resting_hr_baseline_std",
        "resting_hr_zscore",
    ]
    for col in new_cols:
        if col not in cols:
            conn.execute(f"ALTER TABLE daily_metrics ADD COLUMN {col} REAL")
    conn.commit()


def write_back(conn: sqlite3.Connection, df: pd.DataFrame) -> None:
    _ensure_columns(conn)
    for date, row in df.iterrows():
        conn.execute(
            """
            UPDATE daily_metrics SET
                hrv_last_night_avg_baseline_mean = ?,
                hrv_last_night_avg_baseline_std = ?,
                hrv_last_night_avg_zscore = ?,
                resting_hr_baseline_mean = ?,
                resting_hr_baseline_std = ?,
                resting_hr_zscore = ?
            WHERE date = ?
            """,
            (
                row["hrv_last_night_avg_baseline_mean"],
                row["hrv_last_night_avg_baseline_std"],
                row["hrv_last_night_avg_zscore"],
                row["resting_hr_baseline_mean"],
                row["resting_hr_baseline_std"],
                row["resting_hr_zscore"],
                date.strftime("%Y-%m-%d"),
            ),
        )
    conn.commit()


if __name__ == "__main__":
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    df = load_daily_metrics(conn)
    df = add_rolling_baseline(df, "hrv_last_night_avg", invert=False)
    df = add_rolling_baseline(df, "resting_hr", invert=True)
    write_back(conn, df)

    print(f"n days = {len(df)}")
    print(f"days with hrv z-score: {df['hrv_last_night_avg_zscore'].notna().sum()}")
    print(f"days with rhr z-score: {df['resting_hr_zscore'].notna().sum()}")
    print()
    print("hrv_status vs my own hrv z-score (mean, should get more negative as status worsens):")
    print(df.groupby("hrv_status")["hrv_last_night_avg_zscore"].agg(["mean", "count"]))
    print()
    print("correlation of my z-scores with Garmin's training_readiness score:")
    print(df[["hrv_last_night_avg_zscore", "resting_hr_zscore", "training_readiness"]].corr())
    conn.close()
