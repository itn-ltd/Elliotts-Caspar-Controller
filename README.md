# Caspar Controller

> Run and control several CasparCG NDI outputs from one Windows PC, from a web UI and an HTTP API.

[![CI](https://github.com/itn-ltd/Elliotts-Caspar-Controller/actions/workflows/ci.yml/badge.svg)](https://github.com/itn-ltd/Elliotts-Caspar-Controller/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

ITN's build of [Elliott's Caspar Controller](https://github.com/BlueElliott/Elliotts-Caspar-Controller), created by BlueElliott.

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

## Quick start (gallery PCs)

1. **Install Caspar Controller**, with CasparCG Server and the [NDI Runtime](https://ndi.video/tools/). Until it's in Company Portal (#39), use the installer (see [Install](#install)); there's no ITN release yet, so build it from source for now ([Building](#building)).
2. **Click the tray icon**, then **Open Caspar Controller**. On a first run the dashboard takes you to the setup wizard; it's also under **Settings → Setup wizard**.
3. **Follow the wizard:**
   1. the CasparCG path
   2. your outputs and their NDI names (suggested from the controller's name)
   3. the keys for Automator, Companion and panels
   4. **Start All**
   5. a password, so other devices can use it
4. **That's it:** the Dashboard shows each output going live.

### Triggering clips from Companion

1. In **Triggers**, pick the media output and clip, then **Copy** the URL. It already has the **trigger key** in it, which can fire clips and nothing else.
2. In Companion, add a **Generic HTTP → GET** action with that URL. To keep the key out of the URL, leave `&api_key=…` off and put the key in the action's header field as `X-API-Key: <key>` (the `curl` line on the Triggers page shows both).
3. Automator works the same way: it calls the URL.

The URL format is `http://<controller>:5280/api/load?instance=<output>&clip=<clip>&api_key=<key>`, with `&loop=false` to play once. See [Clip triggering](#clip-triggering-companion-panels).

---

## Install

### Requirements

- Windows 10 or 11
- [NDI Runtime](https://ndi.video/tools/), so CasparCG can send NDI

### Installer

Today's releases are the original per-user installer. The per-machine installer and the CasparCG package are #11 and #10, and Company Portal is #39.

1. Run `CasparControllerSetup.exe`.
2. **Lite Caspar Server** (the optional CasparCG download) still points at the original author's releases, so it won't work for ITN builds. Install CasparCG yourself for now; #10 replaces this with a proper package.
3. Launch Caspar Controller from the Start menu.

The app never updates itself: install new versions with the installer.

---

## Setting up by hand

The wizard covers this. Everything it sets, and more, is in **Settings**:

1. **Open the web UI on the controller PC itself:** click the tray icon, or browse to `http://127.0.0.1:5280`.
2. **Set a password:** **Settings → Security**. Until you do, other devices can't use the UI. You can also have one set automatically: see [Default password](#default-password-for-new-installs).
3. **Point it at CasparCG:** **Settings → Outputs → CasparCG Executable**, for example `C:\CasparCG\casparcg.exe`, then **Save Path**. It must be an existing file called `casparcg.exe`, since the app runs it.
4. **Choose the video mode** (default 1080p 25fps).
5. **Add your outputs** under **Instances**, with **+ Add Instance** for each one:
   - **Name**: shown on the dashboard, and used in clip-trigger URLs
   - **NDI Name**: how the output appears on the network (new ones start with the controller's name)
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

- **Crash:** CasparCG exits unexpectedly. **Hang:** it's running but hasn't answered AMCP for the hang timeout (30 s by default). A hung process is stopped as soon as it's found.
- **Never on air:** a hung output that's on program (tally) isn't restarted. The operator is told, and it's restarted once it's off air.
- **Restart:** the instance starts again and gets its startup command (HTML URL or media command) re-sent.
- **Back-off:** waits of 2, 5, 15, 30, then 60 s between attempts; cards show *crashed (1/5)* or *hung (1/5)*, then *restarting*. After 5 restarts in 10 minutes it stops trying: the card shows **failed**, a dashboard banner and the tray say so, and starting it again resets the count. With auto-restart off, a crash shows **crashed**.
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

### API keys

- **Two keys, in Settings → API Keys** (Copy and Rotate each). Both are created on first start.
  - **Trigger key:** fires clips (`/api/load`, `/api/instance/{id}/load`) and nothing else; anything else gets `403`. Give this one to Automator and panels.
  - **Full API key:** everything except account settings. For Companion setups that start and stop outputs, and for other controllers.
- **Send a key either way:**
  - Header (keeps the key out of URLs and logs): `X-API-Key: <key>`
  - Query string, for devices that can only call a URL, such as Automator: `?api_key=<key>`
- **Allowed addresses** (optional): list IPs, subnets or host names, and a key only works from those, so a key copied from a URL is useless elsewhere. Signed-in browsers aren't affected. Other controllers that use this one as a Remote Controller call it with a key, so list them too. Subnets must be exact (`10.0.0.0/24`, not `10.0.0.5/24`). Host names are looked up when the list is saved, at start-up and every 5 minutes in the background; a name that stops resolving keeps its last known address. Imports never change this PC's list. Refused calls are logged with the caller's address, never the key.
- **Rotating** creates a new key straight away. Every device using the old one stops working until it's updated.
- **Password guessing is throttled:** after 5 wrong passwords from one address within a minute, that address is refused (`429`) for a minute, even with the right one, and the event log notes it once. On a shared address (an office NAT, a jump host), one person's typos can briefly lock out the others there.
- **Wrong API keys are logged, not blocked:** keys are random and too long to guess, and blocking would let one device with an old key lock out the rest on its PC. After 5 wrong keys from an address in a minute, the log goes quiet for that address for a minute.
- **Sign-ins last 30 days,** enforced by the server as well as the browser.
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
- **Only fixed actions reach a remote:** status, media, Start/Stop All, restart or stop one output and AMCP, each through its own route (`/api/remote/{n}/...`). No route takes a URL or path, so a remote's key can't be sent anywhere else.

---

## Tray app

- **On launch** the app starts the web server, opens the web UI and sits in the system tray. If the web port is already in use, the tray says so.
- **The tray menu has:** Open Caspar Controller (also a click on the icon), how many instances are live, Restart all instances and Quit. Quitting stops the web server but leaves CasparCG running.
- **CasparCG left running** (for example after the app restarted) is reconnected rather than restarted. **Auto-start** then starts only the instances that aren't running, without touching the live ones.
- **CasparCG runs without a console window.** The web UI's **Console** panel (bottom right of every page) shows each instance's CasparCG log (`log\inst_N\` next to `casparcg.exe`), live. Reading the log files rather than a pipe means CasparCG keeps running when the app quits or is upgraded, and reconnected instances have a console too. To send CasparCG a command, use the AMCP box on its card.

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

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for setting up, running against fake CasparCG servers (`python tools/preview.py`), tests and the workflow.

### Building

```bash
pip install -e ".[build]"
pyinstaller CasparController.spec        # dist\CasparController\
iscc /DMyAppVersion="1.2.3" installer\setup.iss  # installer\Output\CasparControllerSetup.exe
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
