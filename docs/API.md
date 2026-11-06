# Caspar Controller HTTP API

Generated from the app by `python tools/api_docs.py`; don't edit it by hand. While signed in,
`/docs` on any controller shows the same endpoints interactively.

## Authentication

Every endpoint needs one of these, except the two marked **Open**:

- **API key:** the `X-API-Key` header, or `?api_key=` in the URL for devices that can only
  send a URL (Automator, Companion buttons). Both keys are in **Settings → API Keys**.
  - **Full key:** everything in the table below except the **Session** endpoints.
  - **Trigger key:** only the endpoints marked **Trigger**, which fire clips. Give this one to
    Automator and panels.
  - If **Allowed addresses** is filled in, keys only work from those addresses (`403` otherwise).
- **Session:** the browser's sign-in cookie, from `POST /login`. Signed-in browsers can call
  everything. **Session** endpoints (the keys themselves and the password) accept nothing else.
- **First run:** until a password is set, a browser on the PC itself needs no sign-in.

Wrong keys get `401`. Wrong passwords at `/login` are limited: 5 in a minute from one address,
then `429` with `Retry-After` for a minute.

Start, stop and restart return `202` straight away; progress goes to the event log.

## Endpoints

| Method | Path | Access | Does | Parameters |
|---|---|---|---|---|
| GET | `/api/key` | Session | Both API keys (signed-in browsers only). |  |
| POST | `/api/key/rotate` | Session | Make a new key: ?key=trigger for the trigger key, otherwise the full API key. | `key` (`api` or `trigger`, default `api`) |
| POST | `/api/password` | Session | Set or change the web UI password; signs out every other browser. | JSON: `password`, `current_password` |
| POST | `/login` | Open | Sign in with the web UI password; sets the session cookie. | JSON: `password` |
| POST | `/logout` | Session | Sign out this browser. |  |
| GET | `/api/config` | Full key | The config, without the password hash or any API key. |  |
| POST | `/api/config` | Full key | Save Settings fields; anything left out is kept. | JSON object |
| POST | `/api/v1-notice/dismiss` | Full key | Hide the dashboard's "Imported from v1.x" notice. |  |
| GET | `/api/config/export` | Full key | The config as a file, without secrets or this PC's own marks. |  |
| POST | `/api/config/import` | Full key | Replace the settings with an exported config; this PC's keys and password stay. |  |
| GET | `/api/diagnostics` | Full key | A zip for the service desk: config without secrets, versions and logs. |  |
| GET | `/api/log` | Full key | The event log, oldest first. |  |
| GET | `/api/tally` | Full key | Which outputs the vision mixer has on program and preview. |  |
| GET | `/api/events` | Full key | Live status and event log as server-sent events; ?console=<id> adds that output's log. | `console` (integer) |
| GET | `/api/status` | Full key | Every output's state, clip, tally and CasparCG version. |  |
| POST | `/api/server/start` | Full key | Start All: every output not already running (202, runs in the background). |  |
| POST | `/api/server/stop` | Full key | Stop All (202). |  |
| POST | `/api/instance/all/restart` | Full key | Restart every output (202). |  |
| POST | `/api/instance/{inst_id}/restart` | Full key | Start or restart one output (202). |  |
| POST | `/api/instance/{inst_id}/stop` | Full key | Stop one output (202). |  |
| POST | `/api/instance/{inst_id}/amcp` | Full key | Send one raw AMCP command to an output. | JSON: `command` |
| POST | `/api/instance/{inst_id}/auto-restart` | Full key | The card's quick toggle: this instance's override of the global setting. | JSON: `auto_restart` |
| GET | `/api/load` | Trigger | Load a clip into a named media instance via HTTP GET. | `instance` (string); `clip` (string); `loop` (boolean, default `true`) |
| GET | `/api/instance/{inst_id}/load` | Trigger | Load a clip into a media instance by ID via HTTP GET. | `clip` (string); `loop` (boolean, default `true`) |
| GET | `/api/media` | Full key | The clips in the media folder. |  |
| GET | `/api/remotes` | Full key | Each Remote Controller's status, without its key. |  |
| GET | `/api/remote/{idx}/media` | Full key | A Remote Controller's clips. |  |
| POST | `/api/remote/{idx}/test` | Full key | Settings' Test button, for a saved remote: reachable, and is its key accepted? |  |
| POST | `/api/remote/{idx}/server/{action}` | Full key | Start All or Stop All on a Remote Controller. | `action` (`start` or `stop`) |
| POST | `/api/remote/{idx}/instance/{inst_id}/amcp` | Full key | Send a raw AMCP command to an output on a Remote Controller. | JSON: `command` |
| POST | `/api/remote/{idx}/instance/{inst_id}/{action}` | Full key | Restart or stop one output on a Remote Controller. | `action` (`restart` or `stop`) |
| GET | `/api/health` | Open | Open health check for monitoring: versions and each output's state by id. |  |
