# MedTracker chaos results (25 Oct 2026 09:21)

| Failure | How it is detected | Detected after (s) | Back to normal after (s) | Runs | Without self-healing |
|---|---|---|---|---|---|
| App crashes | Watchdog sees the process has exited | 1.1 (max 2.1) | 2.1 (max 3.2) | 3 | Still down after 20 s. Nothing restarted it |
| App freezes | Watchdog: 3 failed health checks in a row | 2.5 (max 2.6) | 11.3 (max 11.4) | 3 | Still frozen after 20 s. Nothing noticed |
| SMS service outage | Circuit breaker: 3 failed checks of the SMS service | 3.9 (max 4.0) | 8.1 (max 8.2) | 3 | Text failed and was never sent, even after the service came back |
| Internet to cloud drops | Sync loop pings the cloud every 3 s | 1.8 (max 2.0) | 10.9 (max 11.1) | 3 | The dose never reached the caregiver. It was dropped |
| Corrupted record in database | Quarantine scan every 3 s validates every record | 1.5 (max 1.6) | 1.5 (max 1.7) | 3 | Bad record stays in the database, mixed with real data |
| All data wiped | Integrity check: healthy medicines suddenly missing | 0.3 (max 0.3) | **Never. Not healed** | 1 | Same: nothing could restore it |
