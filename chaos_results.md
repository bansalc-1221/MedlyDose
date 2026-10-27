# MedTracker chaos results (26 Oct 2026 17:19)

| Failure | How it is detected | Detected after (s) | Back to normal after (s) | Runs | Without self-healing |
|---|---|---|---|---|---|
| App crashes | Watchdog sees the process has exited | 1.5 (max 2.2) | 2.2 (max 2.8) | 3 | Still down after 20 s. Nothing restarted it |
| App freezes | Watchdog: 3 failed health checks in a row | 2.5 (max 2.6) | 11.2 (max 11.4) | 3 | Still frozen after 20 s. Nothing noticed |
| SMS service outage | Circuit breaker: 3 failed checks of the SMS service | 3.8 (max 3.9) | 8.0 (max 8.2) | 3 | Text failed and was never sent, even after the service came back |
| Internet to cloud drops | Sync loop pings the cloud every 3 s | 2.2 (max 2.7) | 11.3 (max 11.8) | 3 | The dose never reached the caregiver. It was dropped |
| Corrupted record in database | Quarantine scan every 3 s validates every record | 1.4 (max 1.6) | 1.4 (max 1.6) | 3 | Bad record stays in the database, mixed with real data |
| All data wiped | Integrity check: healthy medicines suddenly missing | 0.7 (max 0.7) | **Never. Not healed** | 1 | Same: nothing could restore it |
