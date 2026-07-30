"""The actual test: does a good readiness number go with a good easy run?

Takes readiness as a same-day prediction ("today's easy run will be
better-than-usual for the effort") and checks how often that holds, with
bootstrap confidence intervals rather than a bare point estimate -- CLAUDE.md
is explicit that "the score is basically noise for me" is an acceptable
finding here, not a failed analysis, so the CI has to be honest enough to
actually say that if it's true.

Runs the same test for three predictors so Garmin's population-based score
can be compared head to head with this runner's own baseline (the "Me vs.
everyone" question):
  - training_readiness_morning  (Garmin's score, pre-run reading only --
    see confound_flag.py for why "pre-run" specifically)
  - hrv_last_night_avg_zscore   (this runner's own 30-day HRV baseline)
  - resting_hr_zscore           (this runner's own 30-day RHR baseline,
                                  already sign-flipped so + = worse)

Predicted direction: higher readiness / less-negative personal z-score
should predict a *more negative* pace_residual_sec_per_km (faster than
expected at that HR). So a "good" correlation here is negative.
"""

import sqlite3

import numpy as np
import pandas as pd

from garmin.schema import DEFAULT_DB_PATH

N_BOOTSTRAP = 10_000
RNG_SEED = 0


def load_merged(conn: sqlite3.Connection) -> pd.DataFrame:
    act = pd.read_sql(
        "SELECT date, pace_residual_sec_per_km FROM activities WHERE effort_tercile = 'easy'",
        conn,
    )
    dm = pd.read_sql(
        """
        SELECT date, training_readiness_morning,
               hrv_last_night_avg_zscore, resting_hr_zscore
        FROM daily_metrics
        """,
        conn,
    )
    return act.merge(dm, on="date", how="left")


def bootstrap_corr(x: pd.Series, y: pd.Series, n_boot: int = N_BOOTSTRAP, seed: int = RNG_SEED):
    mask = x.notna() & y.notna()
    x, y = x[mask].to_numpy(), y[mask].to_numpy()
    n = len(x)
    if n < 5:
        return {"n": n, "r": None, "ci_low": None, "ci_high": None}

    r = np.corrcoef(x, y)[0, 1]

    rng = np.random.default_rng(seed)
    boot_rs = np.empty(n_boot)
    idx = np.arange(n)
    for i in range(n_boot):
        sample = rng.choice(idx, size=n, replace=True)
        xs, ys = x[sample], y[sample]
        if xs.std() == 0 or ys.std() == 0:
            boot_rs[i] = np.nan
        else:
            boot_rs[i] = np.corrcoef(xs, ys)[0, 1]

    ci_low, ci_high = np.nanpercentile(boot_rs, [2.5, 97.5])
    return {"n": n, "r": r, "ci_low": ci_low, "ci_high": ci_high}


if __name__ == "__main__":
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    df = load_merged(conn)
    conn.close()

    print(f"n easy runs total: {len(df)}")
    print()

    predictors = {
        "training_readiness_morning (Garmin)": "training_readiness_morning",
        "hrv_last_night_avg_zscore (personal)": "hrv_last_night_avg_zscore",
        "resting_hr_zscore (personal)": "resting_hr_zscore",
    }

    print(f"correlation with pace_residual_sec_per_km (negative = readiness/baseline predicts faster-than-expected, as hoped)")
    print(f"95% CI via {N_BOOTSTRAP:,}-resample bootstrap")
    print()
    for label, col in predictors.items():
        result = bootstrap_corr(df[col], df["pace_residual_sec_per_km"])
        if result["r"] is None:
            print(f"{label}: n={result['n']} -- too few paired observations")
            continue
        crosses_zero = result["ci_low"] <= 0 <= result["ci_high"]
        verdict = "CI spans zero -- no reliable signal" if crosses_zero else "CI excludes zero"
        print(f"{label}:")
        print(f"  n={result['n']}  r={result['r']:.3f}  95% CI [{result['ci_low']:.3f}, {result['ci_high']:.3f}]  -- {verdict}")
