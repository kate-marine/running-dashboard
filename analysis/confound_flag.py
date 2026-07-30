"""Fix a temporal-leakage bug and flag the readiness-causes-training loop.

Bug: `daily_metrics.training_readiness` (from `backfill.py`) was extracted as
the *most recent* readiness snapshot for each calendar date. But Garmin
recomputes readiness after that day's activity too
(`inputContext == "AFTER_POST_EXERCISE_RESET"`) -- on 261/280 days there's
both a morning snapshot (`AFTER_WAKEUP_RESET`) and a later, post-run one, and
"most recent" was picking the post-run score on most days. A score computed
*after* a run can't have caused it, and using it to predict that same run is
look-ahead bias -- not the softer "watch talked me into it" loop CLAUDE.md
describes, but a harder, more basic leakage bug sitting upstream of it.

Fix: derive `training_readiness_morning` from the `AFTER_WAKEUP_RESET`
snapshot specifically -- the reading Garmin actually surfaces to the user
first thing, before that day's run. Any predictive test ("does readiness
predict today's run") must use this column, not the old
`training_readiness` one, which stays in the table for descriptive purposes
only (e.g. "what did Garmin's readiness look like by end of day") and should
not be used as a same-day predictor.

This resolves the hard leakage problem but not the full soft version of the
loop -- a runner can still behaviorally react to a bad morning number. Two
things blunt that, worth noting rather than solving:
  1. pace-at-fixed-HR (pace_at_hr.py) is partly loop-resistant by
     construction: "deciding to take it easy" mostly shows up as choosing a
     *lower HR* that day (still landing in the easy tercile), not a slower
     pace *at the same HR* -- that part is much less under conscious
     control.
  2. `training_readiness_morning_level` (POOR/LOW/MODERATE/HIGH) is kept per
     day so the readiness-vs-pace test can be re-run on the subset with a
     valid morning reading, as a sensitivity check.
"""

import json
import sqlite3

from garmin.schema import DEFAULT_DB_PATH


def _extract_morning_snapshot(raw_json: str | None):
    if not raw_json:
        return None
    entries = json.loads(raw_json) or []
    wakeup = [e for e in entries if e.get("inputContext") == "AFTER_WAKEUP_RESET"]
    if not wakeup:
        return None
    return min(wakeup, key=lambda e: e.get("timestampLocal") or "")


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(daily_metrics)")}
    if "training_readiness_morning" not in cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN training_readiness_morning INTEGER")
    if "training_readiness_morning_level" not in cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN training_readiness_morning_level TEXT")
    conn.commit()


def backfill_morning_readiness(conn: sqlite3.Connection) -> dict:
    _ensure_columns(conn)
    rows = conn.execute(
        "SELECT date, training_readiness, training_readiness_raw FROM daily_metrics"
    ).fetchall()

    n_with_morning = 0
    n_changed_level = 0
    for date, latest_score, raw in rows:
        snap = _extract_morning_snapshot(raw)
        morning_score = snap.get("score") if snap else None
        morning_level = snap.get("level") if snap else None
        if morning_score is not None:
            n_with_morning += 1

        conn.execute(
            "UPDATE daily_metrics SET training_readiness_morning = ?, training_readiness_morning_level = ? WHERE date = ?",
            (morning_score, morning_level, date),
        )

    conn.commit()
    return {"n_days": len(rows), "n_with_morning": n_with_morning}


if __name__ == "__main__":
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    result = backfill_morning_readiness(conn)

    import pandas as pd

    df = pd.read_sql(
        "SELECT date, training_readiness, training_readiness_morning FROM daily_metrics",
        conn,
    )
    both = df.dropna(subset=["training_readiness", "training_readiness_morning"])
    diff = both["training_readiness"] - both["training_readiness_morning"]

    print(f"{result['n_with_morning']}/{result['n_days']} days have a valid AFTER_WAKEUP_RESET reading")
    print(f"on {both.shape[0]} days with both: mean(latest - morning) = {diff.mean():.1f}, "
          f"median = {diff.median():.1f}, |diff| >= 10 on {(diff.abs() >= 10).sum()} days")
    conn.close()
