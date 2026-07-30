"""Classify each run as easy / moderate / hard, relative to this runner's
own effort distribution.

Why relative-to-self: Garmin's absolute HR zones (Z1/Z2 = "easy") don't fit
this runner -- essentially none of their runs fall in Z1/Z2, so that
definition would leave an empty "easy" bucket. Garmin's own
`trainingEffectLabel` (TEMPO/LACTATE_THRESHOLD/etc.) reflects the same
mismatch. Instead: rank average HR against this runner's own history and use
the bottom/top tercile as "easy"/"hard" for them specifically. Spot-checked
against duration and `trainingEffectLabel` -- the lowest-avgHR runs are long
duration and Garmin-labeled AEROBIC_BASE/RECOVERY, the highest are short and
labeled TEMPO/LACTATE_THRESHOLD/VO2MAX, so the ranking tracks real effort.

Two overrides on top of the raw tercile:
- Activities under MIN_DURATION_S are excluded from classification entirely
  (fragments / GPS drops / warmup-only segments -- too short for a stable
  avgHR or for pace-at-HR to mean anything).
- Any activity with a real anaerobic training effect
  (anaerobicTrainingEffect >= ANAEROBIC_OVERRIDE) is forced to "hard"
  regardless of its avgHR tercile -- a session with short hard reps and long
  recoveries can average out to a moderate HR while still being a structured
  workout, not a continuous easy day.

Writes results back onto the `activities` table (`effort_tercile`,
`easy_run_exclude_reason`) rather than a separate table, so downstream
analysis scripts can just filter `WHERE effort_tercile = 'easy'`.
"""

import json
import statistics
import sqlite3

from garmin.schema import DEFAULT_DB_PATH

RUNNING_TYPES = {"running", "treadmill_running", "track_running"}
MIN_DURATION_S = 600  # 10 min
ANAEROBIC_OVERRIDE = 2.0


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(activities)")}
    if "effort_tercile" not in cols:
        conn.execute("ALTER TABLE activities ADD COLUMN effort_tercile TEXT")
    if "easy_run_exclude_reason" not in cols:
        conn.execute("ALTER TABLE activities ADD COLUMN easy_run_exclude_reason TEXT")
    conn.commit()


def classify(conn: sqlite3.Connection) -> dict:
    _ensure_columns(conn)

    rows = conn.execute(
        """
        SELECT activity_id, activity_type, duration_s, avg_hr, summary_raw
        FROM activities
        WHERE activity_type IN ({})
        """.format(",".join("?" * len(RUNNING_TYPES))),
        tuple(RUNNING_TYPES),
    ).fetchall()

    # Percentile cutoffs computed only from runs long enough for a stable
    # avgHR reading -- short fragments would otherwise skew the tercile
    # boundaries even though they're excluded from the final classification.
    eligible_hrs = sorted(
        hr for _, _, dur, hr, _ in rows if dur and dur >= MIN_DURATION_S and hr
    )
    n = len(eligible_hrs)
    p33 = eligible_hrs[int(n * 0.33)]
    p67 = eligible_hrs[int(n * 0.67)]

    counts = {"easy": 0, "moderate": 0, "hard": 0, "too_short": 0, "no_hr": 0}

    for activity_id, activity_type, duration_s, avg_hr, summary_raw in rows:
        exclude_reason = None
        tercile = None

        if not duration_s or duration_s < MIN_DURATION_S:
            exclude_reason = "too_short"
            counts["too_short"] += 1
        elif not avg_hr:
            exclude_reason = "no_hr"
            counts["no_hr"] += 1
        else:
            anaerobic = (json.loads(summary_raw) or {}).get("anaerobicTrainingEffect") or 0
            if anaerobic >= ANAEROBIC_OVERRIDE:
                tercile = "hard"
            elif avg_hr <= p33:
                tercile = "easy"
            elif avg_hr >= p67:
                tercile = "hard"
            else:
                tercile = "moderate"
            counts[tercile] += 1

        conn.execute(
            "UPDATE activities SET effort_tercile = ?, easy_run_exclude_reason = ? WHERE activity_id = ?",
            (tercile, exclude_reason, activity_id),
        )

    conn.commit()
    return {"p33_hr": p33, "p67_hr": p67, "n_eligible": n, "counts": counts}


if __name__ == "__main__":
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    result = classify(conn)
    conn.close()
    print(f"tercile cutoffs: easy <= {result['p33_hr']:.0f} bpm, hard >= {result['p67_hr']:.0f} bpm")
    print(f"(computed from {result['n_eligible']} runs >= {MIN_DURATION_S/60:.0f} min)")
    for k, v in result["counts"].items():
        print(f"  {k}: {v}")
