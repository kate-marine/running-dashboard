"""Is the race predictor twitchy?

CLAUDE.md's framing: real fitness doesn't swing day to day, so if the
predicted time bounces around more than fitness plausibly could, the
predictor is reacting to noise (yesterday's sleep, today's HRV reading)
rather than tracking anything real.

Method: for each distance, fit a smooth trend (rolling 14-day mean) as a
stand-in for "real underlying fitness," then look at how far each day's
raw prediction deviates from that smooth trend. The comparison that matters
is day-to-day noise vs. the *implied per-day rate* of the real trend
(whole-block change / number of days) -- not vs. the whole-block change
itself. A single day's noise will always look small next to nine months of
real fitness gain; that comparison is true but misleading. The honest
question is: does the predictor move more in a day than a day's worth of
real fitness gain would explain? Real fitness doesn't move meaningfully
within a single day, so any noise at all is already "too much" in a strict
sense -- the per-day-rate comparison just puts a number on how much.
"""

import sqlite3

import pandas as pd

from garmin.schema import DEFAULT_DB_PATH


def load_predictions(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("SELECT date, distance, predicted_seconds FROM race_predictions", conn)
    df["date"] = pd.to_datetime(df["date"])
    return df.pivot(index="date", columns="distance", values="predicted_seconds").sort_index()


def fmt_time(s: float) -> str:
    s = int(round(s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


if __name__ == "__main__":
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    wide = load_predictions(conn)
    conn.close()

    for distance in ["5k", "10k", "half_marathon", "marathon"]:
        series = wide[distance].dropna()
        trend = series.rolling(14, min_periods=7, center=True).mean()
        residual = series - trend
        day_to_day = series.diff().dropna()

        block_change = trend.iloc[-1] - trend.iloc[0]  # negative = got faster
        n_span_days = (series.index[-1] - series.index[0]).days
        implied_daily_rate = block_change / n_span_days
        noise_std = residual.std()
        day_to_day_std = day_to_day.std()

        print(f"=== {distance} ===")
        print(f"  n days: {len(series)}, range: {series.index.min().date()} .. {series.index.max().date()}")
        print(f"  prediction range: {fmt_time(series.min())} .. {fmt_time(series.max())}")
        print(f"  day-to-day change: mean|Δ|={day_to_day.abs().mean():.1f}s, std={day_to_day_std:.1f}s")
        print(f"  noise around 14-day trend: std={noise_std:.1f}s")
        print(f"  whole-block trend change (14d-smoothed, start->end): {block_change:+.0f}s ({fmt_time(trend.iloc[0])} -> {fmt_time(trend.iloc[-1])}) over {n_span_days} days")
        print(f"  => implied real fitness gain per day: {implied_daily_rate:+.2f}s/day")
        if implied_daily_rate != 0:
            print(f"  => day-to-day noise is {abs(noise_std / implied_daily_rate):.1f}x the size of a day's worth of real trend")
        print()
