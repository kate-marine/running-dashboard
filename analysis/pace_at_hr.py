"""Pace-at-fixed-HR for the easy-run pool.

CLAUDE.md's Part 1 method: don't compare raw pace across days (that just
reflects the training plan), compare pace *at the same effort* -- and control
for weather so a hot run's slower pace-at-HR doesn't get misread as a
bad-recovery day.

Implementation: fit
    pace_sec_per_km ~ avg_hr + heat_stress + is_indoor
across all "easy" runs (see classify_effort.py), using this runner's own
data to find the actual pace/HR relationship rather than assuming one. Each
run's residual from that fit -- actual pace minus what the model expects
given that day's HR/heat/surface -- is the "faster or slower than usual at
the same effort" number. Negative residual = faster than expected (good
day), positive = slower than expected (bad day).

heat_stress = max(0, apparentTemp_F - 60), 0 for indoor (treadmill) runs.
CLAUDE.md calls out heat/humidity specifically as the expected confound;
apparentTemp (NWS "feels like") already folds humidity and wind into one
number, so it's used directly instead of adding separate, collinear
humidity/dew-point terms.

is_indoor controls for treadmill running being a systematically different
surface (no air resistance, belt-paced) rather than a different HR-effort
day -- without it, the model would need to explain a treadmill/outdoor pace
gap using avg_hr, which isn't what's actually driving it.

days_since_start (linear trend over the training block) controls for
overall fitness progression. Without it, heat_stress comes out
*negatively* associated with pace (hotter => modeled as faster), which is
backwards -- because this block runs cold-to-warm (Oct to Jul) while fitness
is presumably also improving over the same months (heat_stress correlates
0.51 with days_since_start). The trend term is a coarse fix -- a straight
line can't capture taper or a plateau -- but leaving it out lets seasonal
fitness gain masquerade as a heat effect, which would bias both this
coefficient and, later, any readiness-vs-pace test that doesn't also account
for where in the block a day falls.
"""

import json
import sqlite3

import pandas as pd
import statsmodels.formula.api as smf

from garmin.schema import DEFAULT_DB_PATH

HEAT_BASELINE_F = 60


def load_easy_runs(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT activity_id, date, activity_type, avg_hr, summary_raw, weather_raw
        FROM activities
        WHERE effort_tercile = 'easy'
        """
    ).fetchall()

    records = []
    for activity_id, date, activity_type, avg_hr, summary_raw, weather_raw in rows:
        summary = json.loads(summary_raw)
        weather = json.loads(weather_raw) if weather_raw else None

        distance_m = summary.get("distance")
        duration_s = summary.get("duration")
        if not distance_m or not duration_s:
            continue

        is_indoor = activity_type == "treadmill_running"
        apparent_temp = (weather or {}).get("apparentTemp")
        heat_stress = 0.0
        if not is_indoor and apparent_temp is not None:
            heat_stress = max(0.0, apparent_temp - HEAT_BASELINE_F)

        records.append(
            {
                "activity_id": activity_id,
                "date": date,
                "avg_hr": avg_hr,
                "pace_sec_per_km": duration_s / (distance_m / 1000),
                "heat_stress": heat_stress,
                "is_indoor": int(is_indoor),
                "apparent_temp": apparent_temp,
            }
        )

    df = pd.DataFrame.from_records(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["days_since_start"] = (df["date"] - df["date"].min()).dt.days
    return df


def fit_pace_at_hr(df: pd.DataFrame):
    model = smf.ols(
        "pace_sec_per_km ~ avg_hr + heat_stress + is_indoor + days_since_start", data=df
    ).fit()
    df = df.copy()
    df["predicted_pace_sec_per_km"] = model.predict(df)
    df["pace_residual_sec_per_km"] = df["pace_sec_per_km"] - df["predicted_pace_sec_per_km"]
    return model, df


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(activities)")}
    for col in ("pace_sec_per_km", "heat_stress", "predicted_pace_sec_per_km", "pace_residual_sec_per_km"):
        if col not in cols:
            conn.execute(f"ALTER TABLE activities ADD COLUMN {col} REAL")
    conn.commit()


def write_back(conn: sqlite3.Connection, df: pd.DataFrame) -> None:
    _ensure_columns(conn)
    for row in df.itertuples():
        conn.execute(
            """
            UPDATE activities
            SET pace_sec_per_km = ?, heat_stress = ?,
                predicted_pace_sec_per_km = ?, pace_residual_sec_per_km = ?
            WHERE activity_id = ?
            """,
            (
                row.pace_sec_per_km,
                row.heat_stress,
                row.predicted_pace_sec_per_km,
                row.pace_residual_sec_per_km,
                row.activity_id,
            ),
        )
    conn.commit()


if __name__ == "__main__":
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    df = load_easy_runs(conn)
    model, df = fit_pace_at_hr(df)
    write_back(conn, df)
    conn.close()

    print(model.summary())
    print()
    print(f"n = {len(df)} easy runs")
    print(f"pace residual std dev: {df['pace_residual_sec_per_km'].std():.1f} sec/km")
    print()
    print("5 fastest-for-effort days (most negative residual):")
    print(df.nsmallest(5, "pace_residual_sec_per_km")[["date", "avg_hr", "apparent_temp", "is_indoor", "pace_residual_sec_per_km"]])
    print()
    print("5 slowest-for-effort days (most positive residual):")
    print(df.nlargest(5, "pace_residual_sec_per_km")[["date", "avg_hr", "apparent_temp", "is_indoor", "pace_residual_sec_per_km"]])
