# MLB Service Time Tracker

A small static website that tracks estimated MLB service time for players
currently on a 40-man roster, and keeps a running log of players once they
drop off a 40-man roster ("previous players"). Data refreshes automatically
once a day via a GitHub Actions cron job.

**Live demo data included.** `docs/data/service_time.json` currently
contains a small hand-written sample dataset (see `scripts/generate_demo_data.py`)
so the site has something to show immediately. Follow the steps below to
switch it over to real, live MLB data.

## How it works

```
scripts/fetch_mlb_data.py     -> thin client for the MLB Stats API (statsapi.mlb.com)
scripts/service_time.py       -> the service-time math (see docstring for the rules modeled)
scripts/update_service_time.py-> orchestrator: pulls current 40-man rosters + transactions,
                                  computes service time, merges into docs/data/service_time.json
scripts/generate_demo_data.py -> builds the bundled sample dataset (no network calls)
docs/                          -> the static site itself (index.html, styles.css, app.js)
docs/data/service_time.json   -> the data file the frontend reads
data/cache/transactions/      -> per-player transaction cache so daily runs are incremental,
                                  not a full history re-pull every time
.github/workflows/update-service-time.yml -> the daily 8am ET cron job
```

Players who are no longer on any team's 40-man roster are **not deleted**
from `service_time.json` -- they're kept with `"on_40_man": false` and their
last-computed service time, which is what makes this a "log of previous
players" rather than just a live roster snapshot.

## Quickstart: run it locally

```bash
cd mlb-service-time-tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the unit tests for the service-time calculation logic
python tests/test_service_time.py

# Pull real data from the live MLB Stats API (this can take a while the
# first time -- it's fetching transaction history for ~1,000+ players)
python scripts/update_service_time.py

# Preview the site
cd docs && python3 -m http.server 8000
# then open http://localhost:8000
```

Useful flags on `update_service_time.py`:
- `--limit N` — only process the first N roster players (fast, for testing)
- `--full-refresh` — ignore the transaction cache and re-pull each player's
  entire history (use this once after changing the rules in `service_time.py`,
  or for your very first real run)

## Deploying it as a live website (recommended: GitHub Pages, free)

1. Create a new GitHub repository and push this project to it:
   ```bash
   git init
   git add .
   git commit -m "Initial commit: MLB service time tracker"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
2. In the repo, go to **Settings → Actions → General → Workflow permissions**
   and select **"Read and write permissions."** This is required so the
   daily Actions run can commit the refreshed data file back to the repo.
   (Skipping this step is the #1 reason the daily job will run but silently
   fail to save its results.)
3. Go to **Settings → Pages**, set **Source: Deploy from a branch**, branch
   `main`, folder `/docs`. Save. GitHub will give you a URL like
   `https://<you>.github.io/<repo>/`.
4. (Recommended) Trigger the workflow once by hand to seed real data instead
   of waiting for the first 8am run: go to the **Actions** tab, select
   **"Daily MLB Service Time Update,"** click **"Run workflow."** Since the
   workflow only does real work when it's actually 8am US/Eastern (see
   below), a manual run outside that window will complete instantly without
   updating data -- if you want to force a real pull immediately, run
   `python scripts/update_service_time.py` locally once and push the
   resulting `docs/data/service_time.json` yourself, or temporarily edit the
   workflow's gate condition.
5. From then on, the site refreshes itself every morning with no further
   action needed.

## About the "8am daily update"

The scheduled workflow (`.github/workflows/update-service-time.yml`) fires
at both 12:00 and 13:00 UTC and internally checks the real US/Eastern
wall-clock hour, so it does real work exactly once a day at 8am Eastern
year-round, correctly handling the twice-yearly Daylight Saving switch
(GitHub Actions cron itself only understands UTC). If you'd rather it run
on a different clock (e.g. 8am Pacific, or a fixed UTC time with no DST
adjustment), edit the `cron:` lines and the `TZ="America/New_York"` value in
that workflow file.

## Data source & methodology (read before trusting the numbers)

Data comes from `statsapi.mlb.com`, the public API that also powers
MLB.com's own stats pages. It's unofficial and undocumented but has been
stable and widely relied on by the baseball-analytics community for years.
No API key is required.

**Service time itself is estimated, not authoritative.** MLB and the
MLBPA jointly maintain the real, official service-time ledger, which isn't
publicly published. This project reconstructs an estimate purely from
public roster-transaction descriptions (recalled/optioned/activated/etc.).
Full methodology, assumptions, and known edge cases (paternity list,
2020 taxi squad, service-time-manipulation grievances, and so on) are
documented in the docstring at the top of `scripts/service_time.py` --
read it before relying on any specific number for a real transaction,
trade evaluation, or contract decision. Notably:

- 172 accrued days = 1 full credited year; days beyond that in a season
  don't add further credit.
- 6.000+ years -> free agency eligible.
- 3.000+ years, or a "Super Two" -> arbitration eligible. The Super Two
  cutoff is only **flagged as a candidate** (players in the historically
  common qualifying window), since computing the real cutoff requires
  league-wide data this API doesn't expose.

## Customizing

- **Colors / branding**: all CSS custom properties live at the top of
  `docs/styles.css` under `.viz-root` (light) and the dark-mode blocks.
  Swap the hex values there.
- **Which players are tracked**: `update_service_time.py` currently tracks
  every player on a 40-man roster league-wide. To scope it to specific
  teams, filter the list returned by `fetch_mlb_data.get_all_40man_players()`.
- **Rules**: all service-time logic (what counts as "active," the 172-day
  cap, thresholds) lives in `scripts/service_time.py` and is covered by
  `tests/test_service_time.py` -- adjust the keyword lists or thresholds
  there and re-run the tests.
