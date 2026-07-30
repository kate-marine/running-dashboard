# Running Dashboard

Personal project checking whether Garmin's readiness score and race predictor
actually mean anything for me, then building my own versions tuned to my own
signals. Full context in [`CLAUDE.md`](CLAUDE.md); process notes as the
project goes in [`PROGRESS.md`](PROGRESS.md).

This uses [`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect)
(cyberjunky), an unofficial wrapper around Garmin Connect's private endpoints.
It's not a supported public API — fine for pulling my own personal data for
personal analysis, but it can break if Garmin changes something server-side.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in GARMIN_EMAIL / GARMIN_PASSWORD in .env
```

## Pulling data

```bash
python -m garmin.backfill --start 2026-03-01 --end 2026-07-28
```

First run logs in with your Garmin credentials and may prompt for an MFA code
on the terminal. The session token is then cached to `data/.garmin_tokens/`
(gitignored) so later runs don't re-authenticate.

Writes to `data/running.db` (SQLite, gitignored). Re-running is safe — rows
are upserted and, by default, dates/activities already in the db are skipped.
Pass `--refresh` to force a re-pull, `--sleep` to change the delay between API
calls (default 1s — this is an unofficial API, no need to hammer it).

Also grab the official Garmin bulk data export once
(Garmin Connect → account settings → export your data) and drop the ZIP in
`data/raw/` as a durable backup. It doesn't carry the derived scores
historically, so it's a backup, not the source.
