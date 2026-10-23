# MedTracker: a self-healing medicine and reminder tracker

MedTracker helps a person (or a caregiver looking after them) take the right medicine at the right time. It shows today's doses, lets you set a reminder with the bell, sends an SMS when a dose is due, keeps a history of what was taken, and shares taken doses with a caregiver view.

A missed or doubled dose can hurt someone, so MedTracker must keep working when things break. It notices failures by itself, works out what went wrong, and recovers with no human restarting anything.

## Run it

Python 3.10 or newer. In PowerShell, from the project folder:

```
python -m venv venv
venv\Scripts\activate
pip install flask requests
```

Open three terminals (each with `venv\Scripts\activate`) and run one line in each:

```
python watchdog.py              # starts the app for you. Do not run app\main.py yourself
python mock_sms\server.py       # pretend SMS service (port 5001)
python mock_cloud\server.py     # pretend caregiver cloud (port 5002)
```

Open http://localhost:5000. Pages: Today, Medicines, Health (live dashboard), Watchdog, Chaos. Caregiver view: http://localhost:5002/doses

## The self-healing mechanisms

| # | Mechanism | Failure it heals | Where |
|---|---|---|---|
| 1 | Watchdog: health checks and automatic restart | App crashes or freezes | `watchdog.py` |
| 2 | Circuit breaker | SMS service fails: stop calling it, test again later, resume | `app/breaker.py` |
| 3 | Offline-first queue with later sync | Internet to the caregiver drops: doses are saved locally and sent when it returns | `app/sync.py` |
| 4 | Self-repairing data (quarantine) | Corrupt records: detect, set aside, keep serving the rest | `app/quarantine.py` |
| 5 | Load shedding (rate limiting) | Traffic spike: one noisy client is refused, everyone else is served | `app/ratelimit.py` |

Every incident is recorded as Detected, Diagnosed, Action taken, Recovered, with timestamps (Health page, and `python show_incidents.py`).

## Break it (chaos)

From the Chaos page, or from a fourth terminal:

```
python chaos.py                     # every failure 3 times with healing ON, once OFF, then prints the results table
python chaos.py run --runs 1        # quicker
python chaos.py inject crash        # crash, freeze, sms, cloud, corrupt, flood, wipe
python chaos.py healing off         # switch self-healing off (and "on" to bring it back)
```

Results are saved to `chaos_results.md` and `chaos_results.json`. The script puts your own data back after the wipe test.

The "Self-healing" switch on the Chaos page turns every mechanism off so the same failure can be shown without protection.

## Results

Run `python chaos.py` on your own machine and paste the table from `chaos_results.md` here. (Run it once, then copy the numbers into the report.)

## What it cannot heal (honest limits)

- **Wiped data.** If every medicine and the history are deleted, MedTracker notices within a second and raises the alarm, but there is no backup, so it cannot get the data back. Nothing can heal data that no longer exists anywhere.
- **The watchdog itself.** Nothing restarts the watchdog. If it dies, the app keeps running but is unguarded (the Health page turns it red).
- **Simulated services.** The SMS and cloud services are local stand-ins. Real services would add network delays and rate limits we have not tested.
- **One machine.** The flood test runs on the same computer as the app, so it shows the idea (the flood is refused, others are served) but not real internet-scale traffic.

## Folder layout

```
watchdog.py, chaos.py, show_incidents.py
mock_sms/server.py, mock_cloud/server.py
app/  main.py db.py breaker.py sync.py quarantine.py ratelimit.py  static/  templates/
```

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 CHHAVI BANSAL.
