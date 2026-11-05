# Elliott's Caspar Controller

> Run and control several CasparCG NDI outputs from one Windows PC, from a web UI and an HTTP API.

[![CI](https://github.com/itn-ltd/Elliotts-Caspar-Controller/actions/workflows/ci.yml/badge.svg)](https://github.com/itn-ltd/Elliotts-Caspar-Controller/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Being refactored for company-wide release via Intune.** The plan is in [`docs/REVIEW.md`](docs/REVIEW.md) and the work is tracked as [issues](https://github.com/itn-ltd/Elliotts-Caspar-Controller/issues) labelled `phase-0` to `phase-4`. Behaviour that a planned ticket will change is marked below.

---

## What it does

The controller launches one CasparCG process per output, each with its own AMCP port and NDI source name. Vision mixers and receivers (Tricaster, vMix, NDI Monitor, etc.) can then pick up whichever outputs they need.

It was built for gallery (TV control room) graphics: Singular.live or other HTML graphics, and media clips, sent out as named NDI sources.

## Features

- **Several CasparCG outputs from one PC**, each a separate process with its own AMCP port and NDI name
- **Sequential start-up**, one output at a time with a 5 s gap, so receivers lock onto the right NDI source
- **Web dashboard**: live status per output, start/stop, current clip with time remaining, clip picker, AMCP command box, event log
- **NDI tally**: cards turn red when an output is on air and amber on preview (needs the NDI Runtime and `ndi-python`)
- **HTTP clip triggering** for Companion, hardware panels and automation, plus a **Triggers** page that builds the URLs
- **Remote Controllers**: monitor and control other controllers from one dashboard
- **Settings page**: CasparCG path, video mode, ports, outputs (add, remove, reorder), media folder, server name, password, API key, config backup
- **Test patterns**: colour and bars PNGs copied into the media folder on start if missing
- **Tray app** that runs the web server, with optional auto-start of CasparCG when the app opens
- **Secured access**: a web UI password and an API key (see [Password and API key](#password-and-api-key))

---

## Install

### Requirements

- Windows 10 or 11
- [NDI Runtime](https://ndi.video/tools/), so CasparCG can send NDI

### Installer

Releases include two files:

| File | What it is |
|---|---|
| `ElliottsCasparControllerSetup.exe` | Installer for the controller |
| `LiteCasparServer.zip` | A pre-configured CasparCG Server (~500 MB) |

1. Run `ElliottsCasparControllerSetup.exe`. It installs for the current user (no admin rights) into `%LOCALAPPDATA%\Programs\Elliott's Caspar Controller`.
2. Optionally tick **Lite Caspar Server**. The installer then downloads `LiteCasparServer.zip` and extracts it (default `C:\CasparCG`). Otherwise, extract the zip yourself.
3. Launch the controller from the Start menu or desktop shortcut.

> Releases are currently published on the original author's repository, and the installer's optional CasparCG download comes from there. Moving to Intune is planned in #10 and #11. The app no longer updates itself; install new versions with the installer (Intune, once #11 is done).

---

## First-time setup

1. **Open the web UI on the controller PC itself:** click **Open Web UI** in the desktop app, or browse to `http://127.0.0.1:5280`.
2. **Set a password:** **Settings → Web UI Password**. Until you do, other devices can't use the UI. You can also have one set automatically: see [Default password](#default-password-for-new-installs).
3. **Point it at CasparCG:** **Settings → CasparCG Executable**, for example `C:\CasparCG\casparcg.exe`, then **Save Path**. It must be an existing file called `casparcg.exe`, since the app runs it.
4. **Choose the video mode** (default 1080p 25fps).
5. **Add your outputs** under **Instances**, with **+ Add Instance** for each one:
   - **Name**: shown on the dashboard, and used in clip-trigger URLs
   - **NDI Name**: how the output appears on the network
   - **Type**: `HTML5` for web graphics (enter the URL) or `Media` for clips (optionally a startup AMCP command)
   - **AMCP Port**: blank to auto-assign (base port + instance number - 1, so reordering never changes it)
6. Click **Save & Regenerate Configs**, then **Start All** on the dashboard.

### What Start All does

1. Skips outputs already running. It reconnects to a CasparCG already answering on an output's port rather than starting a second one.
2. Writes a `casparcg_inst_N.config` for each remaining output next to `casparcg.exe`
3. Starts each in order, waits for AMCP to answer (up to 90 s), then waits 5 s before the next
4. Sends each output its startup command

It never stops a running output, and never touches a CasparCG on a port the app doesn't use. Start, stop and restart run one at a time in the background: the API answers `202` at once, and progress and failures go to the event log and the tray. Stop All also cancels a Start All that's still going.

### Auto-restart

On by default (Settings → **Auto-restart instances that crash or hang**). Each instance can override it, in Settings or with the **↻ Auto** toggle on its card.

- **Crash:** CasparCG exits unexpectedly. **Hang:** it's running but hasn't answered AMCP for the hang timeout (30 s by default).
- **Restart:** the instance starts again and gets its startup command (HTML URL or media command) re-sent.
- **Back-off:** waits of 2, 5, 15, 30, then 60 s between attempts. After 5 restarts in 10 minutes it stops trying: the card shows **failed**, the dashboard and tray say so, and starting it again resets the count.
- **Stops are never undone:** an operator's Stop or Stop All keeps auto-restart off for that instance until it's started again.
- **The console panel** marks each crash or hang, with the lines CasparCG printed before it.

### Output types

| Type | On start |
|---|---|
| `HTML5` | `PLAY 1-1 [HTML] "<url>"`, or `CLEAR 1` if no URL is set |
| `Media` | The startup AMCP command, or `CLEAR 1` if blank. The dashboard shows a clip picker. |

---

## Password and API key

The controls are protected in two ways:

| Who | How they get in |
|---|---|
| People using the web UI | Sign in with the **web UI password** |
| Companion, panels, scripts and other controllers | Send the **API key** with each request |

Only `/login`, `/static/…` and `/api/health` work without either, apart from first-run setup (below).

The API key can't change the password or the key itself: that needs a signed-in browser.

### Web UI password

- **A password is required.** On a new install with no password, the UI and API are open only to a browser **on the controller PC itself**, opened at `http://127.0.0.1:5280` or `http://localhost:5280`, so someone can set one. Other devices, and other websites' pages, get a "set a password first" page or a `401`.
- **Set it** in **Settings → Web UI Password**. You're then asked to sign in, and everyone signs in from then on, including on the controller PC.
- **Change it** in the same place. You need the current password, the new one must differ, and changing it signs out every other browser.
- **Rules:** it can't be blank. Once set, it can be changed but not removed.
- **It's never shown.** It's stored as a salted scrypt hash, so the Settings fields are always empty.
- **Sessions:** signing in lasts until the app restarts, and the browser drops its cookie after 30 days.

### Default password for new installs

Set the environment variable `CASPAR_CONTROLLER_INITIAL_PASSWORD` to give new installs a starting password, for example from Intune:

```powershell
# Machine-wide (run as admin, e.g. in an Intune script)
[Environment]::SetEnvironmentVariable("CASPAR_CONTROLLER_INITIAL_PASSWORD", "<starting password>", "Machine")
```

- **Only used when no password is set.** It never replaces a password someone has chosen, so it's safe to leave in place.
- **Picked up when the app starts.** Restart the app after setting it, or sign out and back in if it still isn't seen.
- **Must change on first sign-in.** The first person to sign in with it goes straight to **Choose a new password**. Until they pick a new one (which must differ), everything else in the UI is blocked. Devices using the API key aren't affected, and a config import can't clear the requirement.
- **Treat it as a temporary secret.** Anyone who can read the PC's environment, or the Intune script, can see it. Use a different value per site or group where you can.

### Forgotten password

1. Quit the controller.
2. In the config file (see [Configuration file](#configuration-file)), set `"web_password_hash": ""` and `"password_change_required": false`.
3. Start the controller. It's back in first-run mode: set a new password from the controller PC. If `CASPAR_CONTROLLER_INITIAL_PASSWORD` is set, that default applies again, with a forced change.

### API key

- **Find it in Settings → API Key** (Copy and Rotate). It's created automatically on first start.
- **Send it either way:**
  - Header (preferred, keeps the key out of URLs and logs): `X-API-Key: <key>`
  - Query string, for devices that can only send a plain URL: `?api_key=<key>`
- **Rotating** creates a new key straight away. Every device using the old one stops working until it's updated.
- **Remote Controllers:** each remote needs *its own* key, entered next to its URL in **Settings → Remote Controllers**. Saved remote keys are never shown again; leave the field blank to keep the saved one.

### Upgrading from an older version

- **If a password was enabled,** it's kept (now hashed), and users sign in with the same password.
- **If no password was enabled,** the controller starts in first-run mode.
- **Either way, existing Companion and panel URLs need `&api_key=<key>` added.**

---

## Clip triggering (Companion, panels)

```
GET http://<controller-ip>:5280/api/load?instance=<name>&clip=<clip>&api_key=<key>
GET http://<controller-ip>:5280/api/load?instance=<name>&clip=<clip>&loop=false&api_key=<key>
```

- **`instance`** is the output's name (not case-sensitive) and must be a `Media` output.
- **Looping is the default;** add `loop=false` to play once.
- **Clip names** can't contain quotes, backslashes or control characters (rejected with `400`), so a trigger can't smuggle in extra AMCP commands.
- **Building URLs:** the **Triggers** page builds them, ready to copy: the URL with `api_key=` first (what Automator uses), then the `curl` form with an `X-API-Key` header. The key is hidden on screen unless you choose to show it.

```bash
curl -H "X-API-Key: <key>" "http://<controller-ip>:5280/api/load?instance=Clipplayer&clip=AMB"
```

### Other API endpoints

All need the key or a signed-in session, except `/api/health`.

| Method | Path | Does |
|---|---|---|
| GET | `/api/health` | Version, server name and up/down, no auth |
| GET | `/api/status` | All outputs: status, tally, current clip |
| GET | `/api/events` | The same status plus the event log, pushed as server-sent events when they change |
| POST | `/api/server/start`, `/api/server/stop` | Start All / Stop All (`202`, runs in the background) |
| POST | `/api/instance/{id}/restart`, `/stop` | One output (`202`) |
| POST | `/api/instance/{id}/amcp` | Send a raw AMCP command: `{"command": "..."}` |
| GET | `/api/instance/{id}/load?clip=` | Load a clip by output number |
| GET | `/api/media` | Clips in the media folder |

---

## Web UI

| Page | Path |
|---|---|
| Dashboard | `/` |
| Triggers | `/triggers` (the old `/http-generator` redirects here) |
| Settings | `/settings` |
| Sign in | `/login` |

- **Default port:** 5280. Change it on the desktop app's port card or in Settings, then restart the app.
- **Other devices** use the controller PC's IP address, for example `http://192.168.1.50:5280`.

### Remote Controllers

- **Add other controllers** in **Settings → Remote Controllers**, with each one's URL and its API key. Save, then **Test** checks the saved remote: that it answers (`/api/health`) and accepts its key.
- **URLs** must be plain `http(s)://host:port`, for example `http://192.168.1.101:5280`, with no path or user name.
- **On the dashboard,** each remote appears with its outputs, clip and time remaining, Start All / Stop All and clip controls. If its key is wrong, the dashboard says so.
- **Only fixed actions reach a remote:** status, media, Start/Stop All, start/stop one output and AMCP, each through its own route (`/api/remote/{n}/...`). No route takes a URL or path, so a remote's key can't be sent anywhere else.

---

## Tray app

- **On launch** the app starts the web server, opens the web UI and sits in the system tray. If the web port is already in use, the tray says so.
- **The tray menu has:** Open Caspar Controller (also a click on the icon), how many instances are live, Restart all instances and Quit. Quitting stops the web server but leaves CasparCG running.
- **CasparCG left running** (for example after the app restarted) is reconnected rather than restarted. **Auto-start** then starts only the instances that aren't running, without touching the live ones.
- **CasparCG runs without a console window.** Its output is in the web UI's **Console** panel (bottom right of every page). Instances that were already running when the app started have no console output.

---

## Configuration file

| | |
|---|---|
| **File** | `%ProgramData%\CasparController\config.json` |
| **From source** | The same, unless `CASPAR_CONTROLLER_HOME` points somewhere else |
| **First run** | Copies a v1.x `elliotts_caspar_config.json` (or `casper` spelling) from beside the app or the repo, keeping the original. With none, `profile.json` in the same folder (e.g. deployed by Intune) supplies the instances and other settings. |

- **Editing:** Settings is easier. The app reads the file once and caches it, so edit it by hand only while the app isn't running.
- **Saving:** atomic (a temp file, then a swap), so a crash mid-save can't leave half a file.
- **Validation:** Save, Import and `profile.json` all go through the same checks:
  - ports in range, and no two instances on the same AMCP port (explicit or automatic)
  - instance and NDI names unique, up to 64 letters, numbers, spaces and `- _ . ( ) '` (no spaces at either end)
  - the CasparCG path must be an existing `casparcg.exe`
- **Secrets:** `api_key`, `web_password_hash` and the remote controllers' keys live here. Treat the file as sensitive.
- **Backup:** Export and Import are under **Settings → Config Backup**. Exports never include the password, the API key or any remote's key. Importing keeps this machine's password, API key and saved remote keys, and its CasparCG path if the imported one doesn't exist here.

**Per-output files next to `casparcg.exe`:**
- `casparcg_inst_N.config`, regenerated on every start, so don't edit them
- `log\inst_N\` and `data\inst_N\`
- `html_cache\inst_N\`, cleared on each start

---

## Development

Python **3.10+** on Windows.

```bash
git clone https://github.com/itn-ltd/Elliotts-Caspar-Controller.git
cd Elliotts-Caspar-Controller
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"          # add ,ndi for NDI tally
set CASPAR_CONTROLLER_HOME=%CD%\.home   # optional: keep dev config out of ProgramData
python -m caspar_controller
```

| | |
|---|---|
| Tests | `pytest` (about 1.5 min; launches fake CasparCG processes) |
| Lint / format | `ruff check .` and `ruff format .` |
| Pre-commit hook (optional) | `pre-commit install` |

- **CI** (`.github/workflows/ci.yml`) runs Lint and Test on every pull request and on merges to `master`.
- **Branches:** branch from `feature/v2-refactor` and open PRs back into it. See [`CLAUDE.md`](CLAUDE.md) and the `code-quality` skill in `.claude/skills/` for conventions.
- **Tests:** `tests/` pins current behaviour, and `tests/test_known_issues.py` lists known bugs as expected failures, each linked to its issue.

### Building

```bash
pip install -e ".[build]"
pyinstaller ElliottsCasparController.spec        # dist\ElliottsCasparController\
iscc /DMyAppVersion="1.2.3" installer\setup.iss  # installer\Output\ElliottsCasparControllerSetup.exe
```

`build.yml` builds and publishes a release when a `v*.*.*` tag is pushed (or run manually).

### Architecture

```
caspar_controller/
├── __main__.py, tray.py   Entry point and tray icon; runs the web server
├── settings.py            Config: Settings model, load, atomic save → %ProgramData%\CasparController
├── auth.py                Password hashing and API keys
├── instances.py           Start, stop, restart and live status of each output
├── remotes.py             Client for other controllers
├── tally.py               NDI tally monitor
├── log.py                 Event log shown on the dashboard
├── caspar/                AMCP client, INFO parsing, casparcg.config, process, test patterns
├── static/                Fonts and icons
├── web/                   The web UI: plain HTML, app.css and app.js, no build step
└── api/                   FastAPI app and one router per area   → http://0.0.0.0:<web-port>
    ├── events.py          /api/events: live status and event log (server-sent events)
    └── pages.py           Serves web/*.html behind sign-in
```

---

## Known limitations

- **Windows only:** process and console handling use Windows APIs.
- **Start-up order matters:** starting outputs in parallel can make receivers latch onto the wrong NDI source, so don't bypass the sequential start.
- **Known bugs** are listed in [`docs/REVIEW.md`](docs/REVIEW.md) and tracked as issues.

## Issues

https://github.com/itn-ltd/Elliotts-Caspar-Controller/issues
