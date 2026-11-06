# Caspar Controller architecture

How the pieces fit, for whoever changes the code next. What each endpoint does is in
[API.md](API.md); setting it up is in the [README](../README.md).

## The big picture

One Windows app per playout PC. It runs several CasparCG Server processes side by side, one
per output, each with its own AMCP port and NDI name, and is controlled from a web UI and an
HTTP API.

```
 Browser (dashboard, Settings)   Automator, Companion, panels   Other controllers
            │                              │                          │
            └──────────── HTTP :5280 ──────┴──────────────────────────┘
                                  │
                ┌─────────────────▼──────────────────┐
                │ Tray app (tray.py)                 │
                │  └─ uvicorn → FastAPI (api/)       │
                │       ├─ AuthMiddleware            │
                │       └─ routers ──┐               │
                │                    ▼               │
                │  InstanceManager (instances.py)    │──── AMCP (TCP) ───► casparcg.exe × N
                │   ├─ one job worker thread         │                       │
                │   ├─ status poll every 0.5 s       │◄── reads log files ───┘
                │   └─ AutoRestart (autorestart.py)  │
                │  Tally (tally.py) ◄── NDI tally echo from each output
                └────────────────────────────────────┘
                     config.json and logs\ in %ProgramData%\CasparController
```

## The agent: tray and web server

- **`__main__.py`** is the exe's entry point. In the frozen exe, stdout and stderr are pointed at
  `devnull` before anything is imported, because uvicorn and logging crash on `None` streams.
- **`tray.py`** starts the log files (`log.write_files`), reads the config, then runs uvicorn
  in a thread and the tray icon in the main thread. Once the server is up it opens the
  browser, reconnects to CasparCG processes left running (`adopt_running`), and runs Start
  All if auto-start is on. Quit stops the server and the tray, but **not CasparCG**: outputs
  stay on air while the app is restarted or upgraded.

## InstanceManager (`instances.py`)

The only thing that creates or controls CasparCG processes.

- **Jobs on one worker thread.** Start, stop and restart, for one output or all, are queued as
  jobs and run one at a time, so two clicks can never race. The API returns `202` straight
  away; progress goes to the event log. Tests call `instances.manager.wait()` after
  submitting one.
- **Start All** starts outputs one at a time, 5 s apart (Stop All cuts the wait short), because NDI receivers can
  latch onto the wrong source if several appear together. Outputs that are already running
  are skipped, so Start All never interrupts one on air.
- **Processes** (`caspar/process.py`): each output gets its own generated
  `casparcg_inst_N.config` (`caspar/config_xml.py`) and is launched with an argument list,
  never a shell string. A process found already listening on an output's port is **adopted**
  rather than started twice.
- **Status poll**: a background thread asks every output for `VERSION` (is it alive?) and, for
  media outputs, `INFO` (what's playing), every 0.5 s, in parallel. The result is swapped in
  whole, so readers never see a half-built poll. `/api/status`, `/api/events` and
  `/api/health` all read this cache and never make AMCP calls themselves.
- **AutoRestart** (`autorestart.py`, a mixin): after each poll it checks for crashed (process
  gone) or hung (no AMCP reply for `hang_timeout` seconds) outputs and restarts them with a
  back-off of 2, 5, 15, 30, then 60 s. After 5 restarts in 10 minutes it gives up: `failed`
  until someone starts it again. A hung output on program in the gallery isn't restarted,
  only reported, so an output on air is never cut by the app.
- **Console**: CasparCG writes its own log files. `caspar/logtail.py` follows each output's
  newest log, including the daily rollover, into an in-memory `Console` (`log.py`) that the
  console panel streams. Reading files rather than a pipe is what lets CasparCG outlive the
  app.

## API (`api/`)

- **`app.py`** builds the FastAPI app: the auth middleware, one router per area (`auth`,
  `config`, `diagnostics`, `events`, `instances`, `media`, `remotes`, `pages`), the static
  files and `/api/health`.
- **Authentication** is one middleware (`api/auth.py`), checked before any router:
  - **Sessions:** a cookie from `POST /login`, held in memory for up to 30 days. A password
    change signs out every other browser.
  - **Keys:** the full API key and the trigger key (`X-API-Key` header or `?api_key=`). The
    trigger key may only fire clips (`/api/load`, `/api/instance/N/load`), and account routes
    are for sessions only.
  - **Allowed addresses** (`clients.py`) limit which addresses may use the keys. Host names are
    resolved in the background, so DNS can never stall a request.
  - **First run:** until a password is set, a browser on the PC itself needs no sign-in.
  - **Throttling** (`throttle.py`): 5 wrong passwords in a minute from one address blocks it for a
    minute. Wrong keys are only rate-limited in the log, because blocking would let one device
    with an old key lock out the rest of its PC.
  - **v1.x changeover:** optional addresses that may fire triggers with no key, each one logged.
- **`events.py`** streams `/api/events` (server-sent events): status, event log and, if asked,
  one output's console. It checks for changes every 0.25 s and only sends when something
  changed. Each stream ends after 5 minutes, or when its sign-in or key stops being valid,
  and the browser reconnects.
- **Remote Controllers** (`remotes.py`, `api/remotes.py`): this controller calls other
  controllers' APIs with their keys, so one dashboard can show and control several PCs. Only
  the saved remotes can be called: no endpoint takes an arbitrary URL.

## Web UI (`web/`)

Plain HTML pages, `app.css` and `app.js`, with no build step. `api/pages.py` serves them behind
sign-in.

- **`app.css`** holds the company theme as tokens (`.dark`), plus every component's styles.
- **`app.js`** is shared by every page:
  - the `api()` fetch helper and toasts
  - `Live`, the page's one `/api/events` connection, which the dashboard and the console
    panel share
  - `Dropdown`, which styles every `<select>` while leaving the real one underneath as the
    source of truth
- **The dashboard** (`index.html`) builds each output card once, then updates only its live parts
  (state, clip, tally), so an open dropdown or a half-typed AMCP command is never reset.
- **Setup wizard** (`welcome.html`) for a first run. **Settings** is the advanced view.

## Configuration (`settings.py`)

- `%ProgramData%\CasparController\config.json` (`CASPAR_CONTROLLER_HOME` overrides it). It's
  read once and cached; `save()` writes atomically (temp file, then `os.replace`) and clears
  the cache.
- **Validation:** the `Settings` pydantic model validates everything an operator can change, so
  the API, imports and profiles all go through the same checks. Secrets (password hash, keys)
  are never in the model, so they can't be set or read through it.
- **First start:** a v1.x config is imported if one is found (per-user installs, `C:\CasparCG`,
  beside the app); otherwise `profile.json` (deployed by Intune) seeds a new install.

## Logs (`log.py`)

- **Event log:** what the dashboard shows. It's kept in memory (the last 200 lines) and in
  `logs\events.log`, whose last lines come back on start.
- **`logs\agent.log`:** the app's Python logging, uvicorn's, and any uncaught error in any thread.
- Both rotate at 1 MB, keeping 5. **Download diagnostics** (`api/diagnostics.py`) zips them with
  the config, minus every secret, and each output's CasparCG log.

## Tally (`tally.py`)

For each output, a metadata-only NDI receiver listens for the tally echo CasparCG sends back
when a vision mixer takes it to program or preview. Without the `ndi` extra or the NDI Runtime,
it quietly does nothing.

## Tests (`tests/`)

- **Characterisation tests:** they pin today's behaviour, so a failing one means the change is
  wrong or the test is being updated on purpose.
- **Fakes:** `fake_amcp.py` is an in-process AMCP server, and `fake_caspar.py` is a stand-in
  `casparcg.exe` that tests really launch.
- **Isolation:** `conftest.py` gives every test a temporary config folder and free ports, and
  guards the kill helper so tests can only kill processes they started.
