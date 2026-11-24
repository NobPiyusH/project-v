# Pod reaper (external kill-timer)

`tools/podkit/reaper.py` is a last line of defence against a forgotten GPU pod (rule 8). It talks only to the RunPod REST API
(`RUNPOD_API_KEY` from the environment or `.env`; never printed) and needs no studio, no Temporal and, for rules (a) and (c), no database.

| Rule | Terminates a pod that... |
|---|---|
| a. `max_wallclock` | has run longer than 3 h. Per pod: put `maxh=2` (or `maxh2.5`) in the pod name, e.g. `projectv-fun-camera-maxh=2` |
| b. `no_lease` | is named `projectv-*`, is older than 15 min and has no unreleased lease with a heartbeat in the last 30 min (reads `gpu_pod_lease` through `tools/gpu_lease`; if the database is unreachable the rule is skipped and a WARN is logged, never guessed) |
| c. `daily_cap` | would push today's (UTC) estimated spend past $3.50 within the next 10 min. Spend = hourly rate x hours run today, carried in the state file, so pods that already ended still count. The cap is the owner's $3.50/day GPU cap; change with `--daily-cap` |

Only `RUNNING` pods are judged (stopped pods bill disk only). **Dry-run is the default**; nothing is terminated without `--apply`.
Exit codes: `0` ok, `1` a terminate failed, `2` API unreachable or no key (a scheduler should alert on non-zero).
Logs: one line per decision, to stdout and `E:\project-data\outputs\pod-reaper\reaper.log`; state in `...\state.json`
(override with `--log`, `--state` or `REAPER_LOG`, `REAPER_STATE`). Spend figures are estimates, not provider bills.

## Try it (safe)

```powershell
powershell -File tools\podkit\reaper.ps1            # dry-run: prints "WOULD TERMINATE ..." lines
powershell -File tools\podkit\reaper.ps1 -Apply     # really terminates
```

## Windows scheduled task, every 10 minutes (run once in an elevated PowerShell, when you are ready)

```powershell
$a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -File C:\dev\project-v\tools\podkit\reaper.ps1 -Apply"
$t = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "ProjectV-PodReaper" -Action $a -Trigger $t -Settings $s
```

Keep `--interval-min` equal to the schedule (default 10). A laptop that sleeps or loses Wi-Fi does not run it; that is why a
non-GPU host is better (below). Alert on failure: Task Scheduler shows "Last Run Result" 2 when the API was unreachable.

## On a free non-GPU host (survives a sleeping laptop)

The script is stdlib-only Python 3.11 with no database needed:

1. Copy `tools/podkit/reaper.py` to the host. Set `RUNPOD_API_KEY` as a secret there. Use a RunPod key restricted to pods if the account allows it.
2. Run `python reaper.py --apply --no-lease-check --state state.json --log reaper.log`. Without the lease database rule (b) is off,
   so rules (a) and (c) are the protection: give every test pod `maxh=` in its name.
3. Schedule every 10 min, e.g. a GitHub Actions workflow with `on: schedule: - cron: "*/10 * * * *"` (the schedule can slip by
   several minutes; keep `maxh` small), a free-tier VM with cron (`*/10 * * * * python3 /opt/reaper.py --apply --no-lease-check ...`), or
   a Cloudflare Worker cron trigger ported to JS. The state file does not persist on GitHub Actions runners: the daily-cap spend then only counts pods that are
   running at that moment (pass a cache or commit the state to a private branch if you need more).
4. A non-zero exit fails the run, which sends the host's normal failure notification.
