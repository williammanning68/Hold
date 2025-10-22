# Troubleshooting Notes

The refactored dashboard and monitoring service automatically reload configuration data and expose logic progression metadata. If you run into issues, review the suggestions below.

## Dashboard Not Loading
- **Symptom:** Browser displays an error banner stating the UI model failed to load.
- **Resolution:** Ensure the API server is running (`python api_server.py`). Confirm that `config.json` exists and is valid JSON. The server automatically recreates defaults using `monitor_config.DEFAULT_CONFIG` when missing.

## Sync Fails With Network Errors
- **Symptom:** The “Run Sync” button reports a failure.
- **Resolution:** Inspect `parliament_monitor.log` for request errors. The scraper honours retry logic configured in `config.json` under `scraping`. Increase `retry_attempts` or `retry_delay` if the Parliament site is slow.

## Keyword Changes Not Reflected
- **Symptom:** Adding or removing keywords does not change alert output.
- **Resolution:** Keyword mutations trigger a configuration reload, but queued monitoring tasks may still be using cached schedules. Run a manual sync after editing keywords or restart the scheduled monitor for immediate effect.

## Database Locked Errors
- **Symptom:** SQLite raises `database is locked` when the monitor and API access the database simultaneously.
- **Resolution:** The monitor uses short-lived write transactions. If the error persists, verify only one long-running export is executing. Consider moving the database to a dedicated path with faster storage (update `database.path` in `config.json`).

## Styling Appears Incorrect
- **Symptom:** Dashboard renders without styling when opened via the file system.
- **Resolution:** Ensure the HTML is served via `api_server.py` so the `/static` assets resolve correctly. If loading the HTML directly, open it from the repository root so relative `static/` paths remain valid.

## Areas for Refinement
- Integrate authentication before exposing the API publicly.
- Persist dashboard refresh preferences per user if multi-user access is required.
- Replace the sparkline renderer with a lightweight charting library (e.g. Chart.js) for richer trend visualisation.
- Extend committee scraping to capture submission deadlines explicitly once the Parliament site schema is confirmed.
