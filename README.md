# MedlyDose: a self-healing medicine and reminder tracker

MedlyDose helps a person (or a caregiver looking after them) take the right medicine at the right time. The Tracker page shows today's doses and a calendar of every day, lets you set a reminder with the bell, sends an SMS when a dose is due, keeps a history of what was taken, and shares taken doses with a caregiver view.

A missed or doubled dose can hurt someone, so MedlyDose must keep working when things break. It notices failures by itself, works out what went wrong, and recovers with no human restarting anything. Built for a self-healing software hackathon (scenario: our own, option E in the brief, a daily-life, high-stakes health app).

Full write-up: see the Resilience Report (`Report/MedlyDose_Resilience_Report.pdf`). The AI Use Declaration is in `Report/MedlyDose_AI_Use_Declaration.pdf`.

## Run it

Python 3.10 or newer. In PowerShell, from the project folder:

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Open three terminals (each with `venv\Scripts\activate`) and run one line in each:

```
python watchdog.py              # starts the app for you. Do not run app\main.py yourself
python mock_sms\server.py       # pretend SMS service (port 5001)
python mock_cloud\server.py     # pretend caregiver cloud (port 5002)
```

Open http://localhost:5000. You see a welcome page first; press Get started and the Tracker opens. Pages: Tracker, Medicines, Health (live dashboard), Watchdog, Chaos. Caregiver view: http://localhost:5002/doses

Only `watchdog.py` is needed for the app itself. The two mock services are needed for the SMS and caregiver features, the chaos demo and `chaos.py`.

## The four self-healing mechanisms

| # | Mechanism | Failure it heals | Where |
|---|---|---|---|
| 1 | Watchdog: health checks every 2 s and automatic restart | App crashes or freezes | `watchdog.py` |
| 2 | Circuit breaker | SMS service fails: stop calling it after 3 failures, test again, resume | `app/breaker.py` |
| 3 | Offline-first queue with later sync | Internet to the caregiver drops: doses are saved locally and sent, oldest first, when it returns | `app/sync.py` |
| 4 | Self-repairing data (quarantine) | Corrupt records: detect, set aside, keep serving the rest | `app/quarantine.py` |

Supporting pieces: an integrity check that notices a sudden loss of data, an automatic reconnect of the SMS and cloud services when self-healing is on (after about 6 s and 8 s), and a Self-healing on/off switch used for the with-and-without comparison.

Every incident is recorded as **Detected, Diagnosed, Action taken, Recovered**, with timestamps (Health page, and `python show_incidents.py`).

## Break it (chaos)

From the Chaos page, or from a fourth terminal:

```
python chaos.py                          # every failure 3 times with healing ON, once OFF, then prints the results table
python chaos.py run --runs 1             # quicker
python chaos.py run --only sms --runs 5 --skip-off   # one failure only
python chaos.py inject crash             # crash, freeze, sms, cloud, corrupt, wipe
python chaos.py restore                  # bring the SMS and cloud services back after breaking them with healing off
python chaos.py healing off              # switch self-healing off (and "on" to bring it back)
python reset_alarm.py                    # clear a stuck "Data lost" alarm (stop watchdog.py first)
```

Results are saved to `chaos_results.md` and `chaos_results.json`. The script puts your own data back after the wipe test. Do not click inside the terminal windows while `chaos.py` runs, because Windows pauses a program when its window is selected.

The Self-healing switch on the Chaos page turns every mechanism off so the same failure can be shown without protection. With it off, the watchdog does not restart, the breaker does not protect, nothing is queued or quarantined, and a downed service stays down until `python chaos.py restore`, or until you switch self-healing back on.

## Results

Measured with `python chaos.py` on one computer: 3 runs per failure with self-healing on (1 for the data wipe), time in seconds from the moment the failure is injected. Maximum in brackets. All 15 healable runs recovered inside the 30 second target with no human action.

| Failure | How it is detected | Detected after (s) | Back to normal after (s) | Without self-healing |
|---|---|---|---|---|
| App crashes | Watchdog sees the process has exited | 1.5 (2.2) | 2.2 (2.8) | Still down after 20 s. Nothing restarted it |
| App freezes | Watchdog: 3 failed health checks in a row | 2.5 (2.6) | 11.2 (11.4) | Still frozen after 20 s. Nothing noticed |
| SMS service outage | Circuit breaker: 3 failed checks of the SMS service | 3.8 (3.9) | 8.0 (8.2) | Text failed and was never sent, even after the service came back |
| Internet to cloud drops | Sync loop pings the cloud every 3 s | 2.2 (2.7) | 11.3 (11.8) | The dose never reached the caregiver. It was dropped |
| Corrupted record in database | Quarantine scan every 3 s validates every record | 1.4 (1.6) | 1.4 (1.6) | Bad record stays in the database, mixed with real data |
| All data wiped | Integrity check: healthy medicines suddenly missing | 0.7 (0.7) | **Never. Not healed** | Same: nothing could restore it |

Times will differ a little on another machine. The raw numbers are in `chaos_results.json`.

## What it cannot heal (honest limits)

- **Wiped data.** If every medicine and the history are deleted, MedlyDose notices within a second and raises the alarm, but there is no backup, so it cannot get the data back. Nothing can heal data that no longer exists anywhere.
- **The watchdog itself.** Nothing restarts the watchdog. If it dies, the app keeps running but is unguarded (the Health page turns it red).
- **Simulated services.** The SMS and cloud services are local stand-ins. Real services would add network delays and rate limits we have not tested.
- **Not tested under heavy load.** We did not build or test protection against a sudden surge of traffic.
- **No login.** Anyone who can open the page on this computer can see and change the medicines.

## Folder layout

```
watchdog.py, chaos.py, reset_alarm.py, show_incidents.py
mock_sms/server.py, mock_cloud/server.py
app/  main.py calendar_api.py db.py breaker.py sync.py quarantine.py  static/  templates/
chaos_results.md, chaos_results.json
Report/  MedlyDose_Resilience_Report.pdf, MedlyDose_AI_Use_Declaration.pdf
```

## AI use

This project was built with the help of Claude (Anthropic) as a coding assistant. The AI Use Declaration submitted with the entry lists what it was asked to do and what was written, changed and checked by hand.

## License

This project is licensed under the MIT License.

Copyright (c) 2026 Chhavi Bansal
