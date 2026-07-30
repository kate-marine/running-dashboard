"""Export the numbers behind the Part 1 write-up into one JSON file for the
report artifact. Pulls from the same tables/columns the analysis scripts
wrote to -- no new computation, just packaging what's already in the db.
"""

import json
import sqlite3

import numpy as np
import pandas as pd

from garmin.schema import DEFAULT_DB_PATH
from analysis.predictor_jumpiness import load_predictions
from analysis.readiness_vs_pace import load_merged, bootstrap_corr
from analysis.race_results import RACES, get_actual_seconds, fmt_time

OUT_PATH = "analysis/part1_report_data.json"


def jumpiness_series(conn):
    wide = load_predictions(conn)
    out = {}
    for distance in ["5k", "10k", "half_marathon", "marathon"]:
        series = wide[distance].dropna()
        trend = series.rolling(14, min_periods=7, center=True).mean()
        out[distance] = [
            {
                "date": d.strftime("%Y-%m-%d"),
                "raw": float(v),
                "trend": None if pd.isna(t) else float(t),
            }
            for d, v, t in zip(series.index, series.values, trend.values)
        ]
    return out


def races_table(conn):
    rows = []
    for date, (distance_label, target_m, mode) in RACES.items():
        pred_row = conn.execute(
            "SELECT predicted_seconds FROM race_predictions WHERE date <= ? AND distance = ? ORDER BY date DESC LIMIT 1",
            (date, distance_label),
        ).fetchone()
        predicted = pred_row[0]
        actual, method = get_actual_seconds(conn, date, target_m, mode)
        pct = (actual - predicted) / predicted * 100
        rows.append(
            {
                "date": date,
                "distance": distance_label,
                "predicted_seconds": predicted,
                "actual_seconds": actual,
                "predicted_fmt": fmt_time(predicted),
                "actual_fmt": fmt_time(actual),
                "pct_diff": round(pct, 1),
                "method": method,
            }
        )
    return rows


def readiness_vs_pace_data(conn):
    df = load_merged(conn)

    predictors = {
        "readiness": ("training_readiness_morning", "Garmin readiness (morning)"),
        "hrv": ("hrv_last_night_avg_zscore", "My HRV baseline"),
        "rhr": ("resting_hr_zscore", "My resting-HR baseline"),
    }
    intervals = {}
    for key, (col, label) in predictors.items():
        result = bootstrap_corr(df[col], df["pace_residual_sec_per_km"])
        intervals[key] = {
            "label": label,
            "n": result["n"],
            "r": None if result["r"] is None else round(float(result["r"]), 3),
            "ci_low": None if result["ci_low"] is None else round(float(result["ci_low"]), 3),
            "ci_high": None if result["ci_high"] is None else round(float(result["ci_high"]), 3),
        }

    scatter = df.dropna(subset=["training_readiness_morning", "pace_residual_sec_per_km"])
    scatter_points = [
        {"x": float(r.training_readiness_morning), "y": float(r.pace_residual_sec_per_km), "date": r.date}
        for r in scatter.itertuples()
    ]

    return {"intervals": intervals, "scatter": scatter_points}


def hrv_status_data(conn):
    df = pd.read_sql(
        "SELECT date, hrv_status, hrv_last_night_avg_zscore FROM daily_metrics WHERE hrv_status IN ('BALANCED','LOW','UNBALANCED')",
        conn,
    )
    df = df.dropna(subset=["hrv_last_night_avg_zscore"])
    return [
        {"date": r.date, "status": r.hrv_status, "z": round(float(r.hrv_last_night_avg_zscore), 3)}
        for r in df.itertuples()
    ]


def effort_classification_data(conn):
    df = pd.read_sql(
        "SELECT effort_tercile, easy_run_exclude_reason FROM activities WHERE activity_type IN ('running','treadmill_running','track_running')",
        conn,
    )
    counts = {}
    for tercile in ["easy", "moderate", "hard"]:
        counts[tercile] = int((df["effort_tercile"] == tercile).sum())
    counts["too_short"] = int((df["easy_run_exclude_reason"] == "too_short").sum())
    counts["no_hr"] = int((df["easy_run_exclude_reason"] == "no_hr").sum())
    return counts


if __name__ == "__main__":
    conn = sqlite3.connect(DEFAULT_DB_PATH)

    data = {
        "jumpiness": jumpiness_series(conn),
        "races": races_table(conn),
        "readiness_vs_pace": readiness_vs_pace_data(conn),
        "hrv_status": hrv_status_data(conn),
        "effort_classification": effort_classification_data(conn),
    }

    conn.close()

    with open(OUT_PATH, "w") as f:
        json.dump(data, f, indent=0)

    print(f"wrote {OUT_PATH}")
    print(f"  jumpiness: {sum(len(v) for v in data['jumpiness'].values())} points across 4 distances")
    print(f"  races: {len(data['races'])} rows")
    print(f"  readiness_vs_pace scatter: {len(data['readiness_vs_pace']['scatter'])} points")
    print(f"  hrv_status: {len(data['hrv_status'])} points")
    print(f"  effort_classification: {data['effort_classification']}")
