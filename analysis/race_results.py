"""Predicted vs. actual for the real races/time trials.

CLAUDE.md is explicit that ~8 data points can't prove the predictor right or
wrong -- this just lays them side by side with wide uncertainty, no verdict.

Three of the six logged activities include real warm-up/cooldown distance
bundled into the same continuous GPS activity around a shorter hard effort
(confirmed with the runner, not GPS noise -- see PROGRESS.md). For those,
`fastest_contiguous_segment` finds the fastest contiguous stretch of laps
that covers the nominal race distance: it slides a start index across the
laps, and for each start accumulates laps until it reaches the target
distance, trimming the overshoot from the final lap assuming uniform pace
within that lap, then keeps whichever start gives the minimum time for
exactly that distance. The other three (marathon, and the two half
marathons run close to the nominal distance) are used as logged, no
extraction needed.
"""

import json
import sqlite3

from garmin.schema import DEFAULT_DB_PATH

# date -> (distance label used in race_predictions, nominal distance in meters,
#          "segment" if the GPS activity needs the fastest-contiguous-window
#          treatment, else "whole")
RACES = {
    "2025-12-07": ("marathon", 42195.0, "whole"),
    "2026-04-11": ("half_marathon", 21097.5, "whole"),
    "2026-05-07": ("half_marathon", 21097.5, "whole"),
    "2026-05-03": ("half_marathon", 21097.5, "segment"),
    "2026-05-23": ("10k", 10000.0, "segment"),
    "2026-05-01": ("5k", 5000.0, "segment"),
}


def fmt_time(s: float) -> str:
    s = int(round(s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fastest_contiguous_segment(laps: list[dict], target_m: float) -> float | None:
    """Return the shortest duration (seconds) of any contiguous run of laps
    covering exactly target_m, trimming the overshoot from the final lap at
    that lap's own pace. None if no window reaches target_m."""
    n = len(laps)
    best_duration = None

    for start in range(n):
        cum_dist = 0.0
        cum_dur = 0.0
        for end in range(start, n):
            cum_dist += laps[end]["distance"]
            cum_dur += laps[end]["duration"]
            if cum_dist >= target_m:
                overshoot = cum_dist - target_m
                lap_dist = laps[end]["distance"]
                lap_dur = laps[end]["duration"]
                if lap_dist > 0:
                    trimmed_dur = cum_dur - lap_dur * (overshoot / lap_dist)
                else:
                    trimmed_dur = cum_dur
                if best_duration is None or trimmed_dur < best_duration:
                    best_duration = trimmed_dur
                break  # no need to keep extending this start further

    return best_duration


def get_actual_seconds(conn: sqlite3.Connection, date: str, target_m: float, mode: str) -> tuple[float, str]:
    row = conn.execute(
        """
        SELECT activity_id, summary_raw, splits_raw FROM activities
        WHERE date = ?
        ORDER BY CAST(json_extract(summary_raw, '$.distance') AS REAL) DESC
        LIMIT 1
        """,
        (date,),
    ).fetchone()
    activity_id, summary_raw, splits_raw = row
    summary = json.loads(summary_raw)

    if mode == "whole":
        return summary["duration"], f"whole activity ({summary['distance']/1000:.2f}km)"

    laps = json.loads(splits_raw)["lapDTOs"]
    duration = fastest_contiguous_segment(laps, target_m)
    return duration, f"fastest {target_m/1000:.1f}km segment within {summary['distance']/1000:.2f}km activity"


if __name__ == "__main__":
    conn = sqlite3.connect(DEFAULT_DB_PATH)

    print(f"{'date':<12} {'distance':<14} {'predicted':>10} {'actual':>10} {'diff':>8}  method")
    for date, (distance_label, target_m, mode) in RACES.items():
        pred_row = conn.execute(
            "SELECT predicted_seconds FROM race_predictions WHERE date <= ? AND distance = ? ORDER BY date DESC LIMIT 1",
            (date, distance_label),
        ).fetchone()
        predicted = pred_row[0] if pred_row else None

        actual, method = get_actual_seconds(conn, date, target_m, mode)

        diff = actual - predicted if predicted is not None else None
        pred_str = fmt_time(predicted) if predicted is not None else "n/a"
        diff_str = f"{diff:+.0f}s" if diff is not None else "n/a"
        print(f"{date:<12} {distance_label:<14} {pred_str:>10} {fmt_time(actual):>10} {diff_str:>8}  {method}")

    conn.close()
