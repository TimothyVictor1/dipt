# Scheduling the DIPT pipeline

There are two ways to run the pipeline on a schedule. Pick one.

* **Dashboard toggle** (easiest): the **Schedule** view runs a background
  daemon that fires a full cycle every N hours (default 24), with an optional
  per-cycle paper cap. Good for a workstation that stays on. It does **not**
  restart after a reboot.
* **OS scheduler** (below): reboot-proof, runs headless, recommended for the
  production machine. Uses the same `python -m dipt.scheduler` entry point.

The scheduler entry point runs the whole chain once and records the run:

```
python -m dipt.scheduler
```

What one run does:

1. Reads the last successful run's start time from `fetch_log` and turns the gap
   into a look-back window (plus one day of overlap). The first ever run, or any
   run made before `migrations/001_support_tables.sql` is applied, uses
   `FETCH_DAYS_BACK` from `.env` instead.
2. Runs `fetch -> parse -> categorise -> summarise -> score -> qa`.
3. Refreshes the static-site data files (`website/data/*.json`,
   `website/public/rss.xml`).
4. Writes the run's outcome (finish time, papers saved, `success` / `failed`)
   back to `fetch_log`.

A run is safe to start while papers from an earlier run are still mid-pipeline:
each stage only picks up papers in the status it expects.

Optional first-run smoke test with a small cap (process at most N papers per
per-paper stage):

```
python -m dipt.scheduler 5
```

Run it with the project root as the working directory and the virtualenv that
has `requirements.txt` installed. All examples below assume:

- project root: `C:\dipt\dipt_build` (Windows) or `/opt/dipt/dipt_build` (Unix)
- python:       the project virtualenv's interpreter

Adjust both to your install.

---

## Windows Task Scheduler (production machine)

### One-off setup with `schtasks`

Run in an elevated PowerShell. This creates a task that runs every 12 hours,
starting at 06:00, whether or not a user is logged in:

```
schtasks /Create ^
  /TN "DIPT pipeline" ^
  /TR "\"C:\dipt\dipt_build\.venv\Scripts\python.exe\" -m dipt.scheduler" ^
  /SC HOURLY /MO 12 /ST 06:00 ^
  /RL HIGHEST /RU SYSTEM ^
  /F
```

Notes:

- `/SC HOURLY /MO 12` = every 12 hours. `/ST 06:00` sets the first run, so runs
  land at 06:00 and 18:00.
- `/RU SYSTEM` runs without a logged-in user. If the pipeline needs a user
  profile (for example a per-user Ollama install), use `/RU <user> /RP *` and
  type the password when prompted.
- Task Scheduler does not set a working directory. `python -m dipt.scheduler`
  does not need one because `.env` is read from the package's own location, but
  if you wrap the call in a `.bat` file, `cd /d C:\dipt\dipt_build` first.

### Wrapper batch file (recommended)

`C:\dipt\run-dipt.bat`:

```
@echo off
cd /d C:\dipt\dipt_build
".venv\Scripts\python.exe" -m dipt.scheduler >> logs\scheduler.log 2>&1
```

Then point the task at the batch file:

```
schtasks /Create /TN "DIPT pipeline" /TR "C:\dipt\run-dipt.bat" ^
  /SC HOURLY /MO 12 /ST 06:00 /RL HIGHEST /RU SYSTEM /F
```

### Check, run, remove

```
schtasks /Query  /TN "DIPT pipeline" /V /FO LIST
schtasks /Run    /TN "DIPT pipeline"
schtasks /Delete /TN "DIPT pipeline" /F
```

### GUI equivalent

Task Scheduler -> Create Task -> Triggers -> New -> "On a schedule", Daily,
Recur every 1 day, "Repeat task every 12 hours" for "1 day". Actions -> New ->
Start a program -> the python path, argument `-m dipt.scheduler`, "Start in"
`C:\dipt\dipt_build`. General -> "Run whether user is logged on or not".

---

## macOS / Linux

### cron (12-hour interval)

`crontab -e`:

```
# DIPT pipeline: 06:00 and 18:00 daily
0 6,18 * * * cd /opt/dipt/dipt_build && /opt/dipt/dipt_build/.venv/bin/python -m dipt.scheduler >> /opt/dipt/dipt_build/logs/scheduler.log 2>&1
```

### launchd (macOS, survives reboots, catches up missed runs)

`~/Library/LaunchAgents/se.bth.dipt.pipeline.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>se.bth.dipt.pipeline</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/dipt/dipt_build/.venv/bin/python</string>
    <string>-m</string>
    <string>dipt.scheduler</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/opt/dipt/dipt_build</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key>
  <string>/opt/dipt/dipt_build/logs/scheduler.log</string>
  <key>StandardErrorPath</key>
  <string>/opt/dipt/dipt_build/logs/scheduler.log</string>
</dict>
</plist>
```

Load it:

```
launchctl load ~/Library/LaunchAgents/se.bth.dipt.pipeline.plist
launchctl start se.bth.dipt.pipeline      # run once now to test
launchctl unload ~/Library/LaunchAgents/se.bth.dipt.pipeline.plist   # to remove
```

---

## Before the first scheduled run

1. Apply the support-tables migration as the database owner so run history and
   incremental fetch work:

   ```
   psql -U postgres -d dipt -f migrations/001_support_tables.sql
   ```

2. Create the log directory referenced by your wrapper (`logs\` /
   `logs/`).

3. Do one manual `python -m dipt.scheduler 3` and confirm a row appears:

   ```
   psql -U postgres -d dipt -c "SELECT * FROM fetch_log ORDER BY id DESC LIMIT 3;"
   ```

## Publishing the public site after a run

The scheduler regenerates `website/data/*.json`. To turn that into a deployed
site, have your host rebuild on change. With Cloudflare Pages connected to the
repo, committing the updated data files triggers a build. To publish from the
production machine directly, append to the wrapper:

```
cd /opt/dipt/dipt_build/website && npm ci && npm run build && npx wrangler pages deploy out
```
