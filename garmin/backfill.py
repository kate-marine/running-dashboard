"""Pull daily metrics, race predictions, and activities for a date range.

Usage:
    python -m garmin.backfill --start 2026-03-01 --end 2026-07-28

Re-running is safe: rows are upserted keyed by date / activity_id, so a
second run only fills in gaps unless --refresh is passed. Sleeps between
calls by default since this rides on an unofficial, undocumented API --
be polite to it.

Note on field extraction: `daily_metrics.resting_hr` and `.training_status`,
and the `race_predictions` table, are parsed from Garmin's raw (untyped)
response shape based on the known conventions of this API, not from a
live response seen in this environment (no credentials here to test
against). The full raw JSON is always stored alongside, so if a scalar
column comes back NULL on a real run, the fix is to inspect the matching
`*_raw` column and adjust the parsing helpers below -- the data itself
isn't lost.
"""

import argparse
import json
import logging
import time
from datetime import date, datetime, timedelta

from garmin.client import get_client
from garmin.schema import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill")

DISTANCE_FIELDS = {
    "5k": "time5K",
    "10k": "time10K",
    "half_marathon": "timeHalfMarathon",
    "marathon": "timeMarathon",
}


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _extract_readiness_score(readiness_list):
    if not readiness_list:
        return None
    latest = max(readiness_list, key=lambda r: r.get("timestamp") or "")
    return latest.get("score")


def _extract_hrv(hrv):
    if not hrv:
        return None, None
    summary = hrv.get("hrvSummary") or {}
    return summary.get("status"), summary.get("lastNightAvg")


def _extract_sleep_seconds(sleep):
    dto = (sleep or {}).get("dailySleepDTO") or {}
    return dto.get("sleepTimeSeconds")


def _extract_rhr(rhr):
    try:
        metrics = rhr["allMetrics"]["metricsMap"]["WELLNESS_RESTING_HEART_RATE"]
        if metrics:
            return metrics[0].get("value")
    except (KeyError, TypeError, IndexError):
        pass
    return None


def _extract_training_status(status):
    try:
        latest = status["mostRecentTrainingStatus"]["latestTrainingStatusData"]
        first_device = next(iter(latest.values()))
        return first_device.get("trainingStatusFeedbackPhrase") or str(
            first_device.get("trainingStatus")
        )
    except (KeyError, TypeError, StopIteration, AttributeError):
        return None


def _safe_call(fn, *args, label=""):
    try:
        return fn(*args)
    except Exception as e:
        log.warning("  %s failed: %s", label, e)
        return None


def pull_daily_metrics(client, conn, d: date, refresh: bool, sleep_s: float):
    date_str = d.isoformat()

    if not refresh:
        existing = conn.execute(
            "SELECT 1 FROM daily_metrics WHERE date = ?", (date_str,)
        ).fetchone()
        if existing:
            log.info("skip %s (already pulled)", date_str)
            return

    log.info("pulling daily metrics for %s", date_str)

    readiness = _safe_call(client.get_training_readiness, date_str, label="training_readiness")
    hrv = _safe_call(client.get_hrv_data, date_str, label="hrv")
    sleep_data = _safe_call(client.get_sleep_data, date_str, label="sleep")
    rhr = _safe_call(client.get_rhr_day, date_str, label="rhr")
    training_status = _safe_call(client.get_training_status, date_str, label="training_status")

    hrv_status, hrv_last_night_avg = _extract_hrv(hrv)

    conn.execute(
        """
        INSERT INTO daily_metrics (
            date, training_readiness, hrv_status, hrv_last_night_avg,
            resting_hr, sleep_seconds, training_status,
            training_readiness_raw, hrv_raw, rhr_raw, sleep_raw, training_status_raw,
            fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            training_readiness=excluded.training_readiness,
            hrv_status=excluded.hrv_status,
            hrv_last_night_avg=excluded.hrv_last_night_avg,
            resting_hr=excluded.resting_hr,
            sleep_seconds=excluded.sleep_seconds,
            training_status=excluded.training_status,
            training_readiness_raw=excluded.training_readiness_raw,
            hrv_raw=excluded.hrv_raw,
            rhr_raw=excluded.rhr_raw,
            sleep_raw=excluded.sleep_raw,
            training_status_raw=excluded.training_status_raw,
            fetched_at=excluded.fetched_at
        """,
        (
            date_str,
            _extract_readiness_score(readiness),
            hrv_status,
            hrv_last_night_avg,
            _extract_rhr(rhr),
            _extract_sleep_seconds(sleep_data),
            _extract_training_status(training_status),
            json.dumps(readiness),
            json.dumps(hrv),
            json.dumps(rhr),
            json.dumps(sleep_data),
            json.dumps(training_status),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    time.sleep(sleep_s)


def pull_race_predictions(client, conn, start: date, end: date):
    log.info("pulling race predictions for %s..%s", start, end)
    raw = _safe_call(
        client.get_race_predictions,
        start.isoformat(),
        end.isoformat(),
        "daily",
        label="race_predictions",
    )
    entries = raw if isinstance(raw, list) else (raw or {}).get("data") or []

    fetched_at = datetime.now().isoformat()
    rows = 0
    for entry in entries:
        date_str = entry.get("calendarDate") or entry.get("date")
        if not date_str:
            continue
        for distance, field in DISTANCE_FIELDS.items():
            seconds = entry.get(field)
            if seconds is None:
                continue
            conn.execute(
                """
                INSERT INTO race_predictions (date, distance, predicted_seconds, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date, distance) DO UPDATE SET
                    predicted_seconds=excluded.predicted_seconds,
                    fetched_at=excluded.fetched_at
                """,
                (date_str, distance, seconds, fetched_at),
            )
            rows += 1
    conn.commit()
    log.info("  wrote %d race prediction rows", rows)


def pull_activities(client, conn, start: date, end: date, refresh: bool, sleep_s: float):
    log.info("pulling activity list for %s..%s", start, end)
    activities = _safe_call(
        client.get_activities_by_date,
        start.isoformat(),
        end.isoformat(),
        label="activities_by_date",
    ) or []
    log.info("  found %d activities", len(activities))

    for activity in activities:
        activity_id = str(activity.get("activityId"))
        if not refresh:
            existing = conn.execute(
                "SELECT 1 FROM activities WHERE activity_id = ?", (activity_id,)
            ).fetchone()
            if existing:
                continue

        date_str = (activity.get("startTimeLocal") or "")[:10]
        activity_type = ((activity.get("activityType") or {}).get("typeKey"))

        log.info("  activity %s (%s, %s)", activity_id, date_str, activity_type)

        weather = _safe_call(client.get_activity_weather, activity_id, label="weather")
        hr_zones = _safe_call(client.get_activity_hr_in_timezones, activity_id, label="hr_timezones")
        splits = _safe_call(client.get_activity_splits, activity_id, label="splits")

        conn.execute(
            """
            INSERT INTO activities (
                activity_id, date, activity_type, duration_s, distance_m,
                avg_hr, avg_speed_mps, summary_raw, weather_raw, hr_timezones_raw,
                splits_raw, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(activity_id) DO UPDATE SET
                date=excluded.date,
                activity_type=excluded.activity_type,
                duration_s=excluded.duration_s,
                distance_m=excluded.distance_m,
                avg_hr=excluded.avg_hr,
                avg_speed_mps=excluded.avg_speed_mps,
                summary_raw=excluded.summary_raw,
                weather_raw=excluded.weather_raw,
                hr_timezones_raw=excluded.hr_timezones_raw,
                splits_raw=excluded.splits_raw,
                fetched_at=excluded.fetched_at
            """,
            (
                activity_id,
                date_str,
                activity_type,
                activity.get("duration"),
                activity.get("distance"),
                activity.get("averageHR"),
                activity.get("averageSpeed"),
                json.dumps(activity),
                json.dumps(weather),
                json.dumps(hr_zones),
                json.dumps(splits),
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        time.sleep(sleep_s)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", default=date.today().isoformat(), help="YYYY-MM-DD, default today")
    parser.add_argument("--db", default=None, help="path to sqlite db, default data/running.db")
    parser.add_argument("--sleep", type=float, default=1.0, help="seconds to sleep between API calls")
    parser.add_argument("--refresh", action="store_true", help="re-pull dates/activities already in the db")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    from pathlib import Path

    db_path = Path(args.db) if args.db else None
    conn = init_db(db_path) if db_path else init_db()

    client = get_client()

    for d in daterange(start, end):
        pull_daily_metrics(client, conn, d, args.refresh, args.sleep)

    pull_race_predictions(client, conn, start, end)
    pull_activities(client, conn, start, end, args.refresh, args.sleep)

    conn.close()
    log.info("done")


if __name__ == "__main__":
    main()
