# Caspar Controller: Code Review and Refactor Plan

| | |
|---|---|
| **Reviewed** | `master` @ `8f22a34` (v1.1.1), 2026-11-03 |
| **Scope** | App code, installer, build pipeline, packaging, scripts |
| **Goal** | Safe, easy to install and supportable for company-wide release via Intune |
| **Direction** | **Approach A**: a local agent on each playout PC serves the web UI and an authenticated API. Remote Controllers link the agents. |
| **Updated** | 2026-11-05: gallery answers and the templates request (4.13, 4.14) |

---

## 1. Summary

The app works: it runs several CasparCG servers on one PC, each with its own NDI output, controlled from a browser. It is **not ready for company deployment**:

1. **Security.** The API has no authentication. That allows remote code execution and arbitrary file reads. The password only protects the HTML pages.
2. **Deployment.** The installer is per-user, downloads 500 MB at install time and the app updates itself from a personal GitHub account. An Intune (SYSTEM) install would be invisible to users.
3. **Structure.** Start/stop logic is duplicated between the desktop GUI and the web server. About 1,150 lines of HTML/CSS/JS sit in Python strings. There are no tests.

The plan keeps the product (web UI, HTTP triggering, Remote Controllers) and rebuilds the internals around **one instance manager** behind **one authenticated API**. The desktop window becomes a tray icon and the CasparCG consoles move into a web UI side panel.

**Fix before anyone else installs it:**

| # | Issue | Where |
|---|---|---|
| 1 | Unauthenticated RCE: instance name injected into a `cmd /k` command line | [process_manager.py:57-58](../elliotts_casper_controller/process_manager.py#L57-L58) |
| 2 | Unauthenticated file read via `/static/C:/...` | [core.py:523-528](../elliotts_casper_controller/core.py#L523-L528) |
| 3 | `/api/*` skips auth; `/api/config` returns the password in plain text | [core.py:191](../elliotts_casper_controller/core.py#L191), [core.py:855-857](../elliotts_casper_controller/core.py#L855-L857) |
| 4 | Self-updater silently runs unsigned installers from a personal repo | [gui_launcher.py:510-624](../elliotts_casper_controller/gui_launcher.py#L510-L624) |
| 5 | Per-user installer with internet download; incompatible with Intune | [installer/setup.iss](../installer/setup.iss) |

---

## 2. Target architecture

```
 Playout PC                                                  Browser / Companion / panel
┌──────────────────────────────────────────────────┐
│  Caspar Controller agent  (tray app, auto-start) │        GET /api/load?...&api_key=…
│                                                  │◄──────  X-API-Key: …
│  ┌──────────────┐   ┌──────────────────────────┐ │
│  │ FastAPI      │──►│ InstanceManager (single  │ │        Web UI (static files)
│  │  /api/*  key │   │ owner, lock, watchdog)   │ │◄──────  session cookie
│  │  /ui  static │   └──────┬─────────┬─────────┘ │
│  │  /api/events │          │         │           │
│  │   (SSE)      │   ┌──────▼───┐ ┌───▼────────┐  │        Other agents (Remote Controllers)
│  └──────────────┘   │ Caspar   │ │ NDI tally  │  │◄─────►  agent to agent, API key
│                     │ process  │ └────────────┘  │
│                     │ + stdout │──► console ring │
│                     │   pipe   │    buffers      │
│                     └────┬─────┘                 │
└──────────────────────────┼───────────────────────┘
                           ▼
             casparcg.exe × N  (one per NDI output)
```

- **One owner of state.** Only `InstanceManager` starts, stops or inspects CasparCG.
- **One API.** The UI, triggers and Remote Controllers all use the same authenticated endpoints.
- **No hidden windows.** CasparCG output is piped into the agent and streamed to the browser.
- **Config in `%ProgramData%`.** Intune installs and updates the app; it never updates itself.

---

## 3. Findings

Severity: 🔴 high · 🟠 medium · 🟡 low.

### 3.1 Correctness (`code-review` pass)

Reproduced from the review pass, which concentrated on the recent status and clip-info changes. Section 3.2 covers the rest.

1. **[core.py:102](../elliotts_casper_controller/core.py#L102)**: `_parse_layer_info` expects an XML layout CasparCG does not send: `type` is read as an attribute of `<producer>`, and the code looks for `<filename>`, `<time>` and `<duration>` children directly under `foreground/producer`.
   *Failure scenario:* In CasparCG 2.0/2.1 the producer type is a `<type>` child element, so `fg.get('type')` always returns `''`, the empty-producer check never fires and `producer` is reported as `''`. Those versions also report frames (`<frame-number>`, `<nb_frames>`, `<file-frame-number>`), not `<time>` or `<duration>`, so time and duration are always 0 and the progress bar and remaining time never show. In 2.3, which this project targets (it generates `<ndi>` consumer and `<html><cache-path>` configs), `foreground/producer` is a text leaf such as `ffmpeg` and the file data sits under `foreground/file/{path,name,time}`, so `fg.find('filename')` is None, clip is None, and the card shows nothing for a clip that is playing.

2. **[core.py:149](../elliotts_casper_controller/core.py#L149)**: Web UI liveness now depends on `INFO 1-1` returning 201 instead of `VERSION`, which brings back the false-offline problem v1.0.8 fixed, and the web UI can now disagree with the GUI, which still uses `ping()`/`VERSION`.
   *Failure scenario:* The server is up, but `INFO 1-1` returns 4xx (unsupported on that build, channel 1 not created yet during startup, etc.) or the large XML reply misses the 0.05s + 0.5s read window. `is_live` is then False, `/api/status` reports `stopped`, the card offers "▶ Start" and the header says "Start Remaining". Clicking it calls `/restart` and kills a live on-air output, while the tkinter GUI ([gui_launcher.py:863](../elliotts_casper_controller/gui_launcher.py#L863), `VERSION`) shows the same instance as running.

3. **[installer/setup.iss:279](../installer/setup.iss)**: A silent auto-update (`/VERYSILENT` from `gui_launcher._apply_update`) re-runs the caspar component. It downloads 500 MB again and runs `Expand-Archive -Force` into the default `{sd}\CasparCG`, because the user's chosen Caspar folder is never saved (no `RegisterPreviousData`/`GetPreviousData`).
   *Failure scenario:* A user installs with the "full" type and puts the Lite Caspar Server in `D:\Caspar`. Inno remembers the selected components (`UsePreviousComponents` defaults to yes), so every one-click update downloads about 500 MB silently and extracts a second copy to `C:\CasparCG`, overwriting any edited files there. The app keeps pointing at `D:\Caspar`. Failure MsgBoxes are suppressed by `/SUPPRESSMSGBOXES`.

4. **[.github/workflows/build.yml:50](../.github/workflows/build.yml#L50)**: The bootloader is built from PyInstaller's master branch (`git clone --depth 1`) and copied over the pip-installed release (`pyinstaller>=6.0.0`), so the bootloader and PyInstaller versions can differ.
   *Failure scenario:* When master changes the bootloader/archive protocol (CArchive/PKG format, splash or runtime-option handling) ahead of the latest PyPI release, the tagged build succeeds and the exe check passes (it only tests that the file exists), but the shipped exe fails at launch with "Cannot open PyInstaller archive" or a similar error. The fix is to clone the tag matching `PyInstaller.__version__`.

5. **[.github/workflows/build.yml:282](../.github/workflows/build.yml)**: The release-notes Issues link points to `Elliotts-Casper-Controller` (Casper), but the repository is `Elliotts-Caspar-Controller`.
   *Failure scenario:* Every GitHub release body links to `https://github.com/BlueElliott/Elliotts-Casper-Controller/issues`, which returns 404.

6. **[core.py:1173](../elliotts_casper_controller/core.py#L1173)**: The clip name, taken from the CasparCG `<filename>` (which can be an arbitrary path or stream URL), is put into `innerHTML` without escaping.
   *Failure scenario:* Anyone can reach the unauthenticated `/api/instance/{id}/amcp` or `/api/load` endpoints and send `PLAY 1-1 "http://x/<img src=x onerror=alert(1)>"`. On the next 2s poll, `basename()` of that string goes straight into every viewer's instance card and the script runs. Ordinary filenames containing `&` also render as broken entities.

7. **[core.py:154](../elliotts_casper_controller/core.py#L154)**: Threads still running after `join(timeout=1.5)` keep writing into the `live` and `layer_info` dicts after those exact objects have been published to `_status_cache`, outside `_status_lock`.
   *Failure scenario:* A slow instance takes about 1.6s (connect timeout 1.0 + sleep 0.05 + 0.5 recv). The snapshot is published without its entry, so the instance shows "stopped". The late thread then sets `live[id]=True` and `layer_info[id]` in the cached dict outside the lock while `api_status` is copying it. The instance's state flips between polls, and for a window `live[id]` can be True with no layer entry.

8. **[core.py:1344](../elliotts_casper_controller/core.py#L1344)**: `remoteInstanceCard` ignores the new `inst.layer` field, so remote-controller instance cards never show the loaded clip or time remaining, even though the remote's `/api/status` now returns it.
   *Failure scenario:* An operator watching a remote controller's media instance sees no clip or countdown, while the same instance viewed on its own controller shows it. The v1.1.1 feature only works for local instances.

9. **[core.py:110](../elliotts_casper_controller/core.py#L110)**: The "strip numeric suffix" heuristic drops any trailing `_<digits>` segment, so real clip names are cut short.
   *Failure scenario:* A clip whose reported name has no extension, such as `PROMO_2024` or `INTERVIEW_2` (2.3 reports the extension-less name/path), is shown as `PROMO` or `INTERVIEW`. An operator can mistake which clip is on air.

10. **[core.py:151](../elliotts_casper_controller/core.py#L151)**: Redundant code: the `if is_live else {...}` fallback in `_fetch` copies the `empty` dict literal from `_parse_layer_info`, which already returns `empty` for non-201 replies. `import os as _os` (line 2) duplicates the module-level `import os` at line 509, and the 1s width transition on `.clip-progress-bar` never runs because the bar is rebuilt on every render.
    *Failure scenario:* The empty-layer shape is defined in two places, so a field added to one will be missing from the other. The extra `os` alias and the transition CSS that never fires are dead weight. Simpler: `layer_info[id] = _parse_layer_info(raw)`, use `os`, and drop the transition.

### 3.2 Additional findings (full-codebase read)

#### Security

| Sev | Finding | Where |
|---|---|---|
| 🔴 | **Command injection.** The instance name goes into `cmd /k "… title <name> && casparcg.exe …"` with only `"` and `&` stripped, so `\|`, `<`, `>` and `^` still work. An unauthenticated `POST /api/config` plus Start runs any command. (Checked by building the command string; not executed.) | [process_manager.py:57-58](../elliotts_casper_controller/process_manager.py#L57-L58) |
| 🔴 | **File read.** `os.path.join(STATIC_DIR, filename)` lets `C:/…` or `../` escape the folder. `/static` skips auth. | [core.py:523-528](../elliotts_casper_controller/core.py#L523-L528) |
| 🔴 | **No API auth.** Every `/api/*` route is exempt: start/stop, raw AMCP, config overwrite. | [core.py:191](../elliotts_casper_controller/core.py#L191) |
| 🔴 | **Password exposed.** Returned by `GET /api/config`, stored in plain text, compared with `==`. | [core.py:855-857](../elliotts_casper_controller/core.py#L855-L857), [core.py:1042](../elliotts_casper_controller/core.py#L1042) |
| 🟠 | **SSRF.** `/api/remote/test` fetches any URL; the proxy builds `f"{url}{req.path}"`, so `@evil.host/x` changes the host. | [core.py:967-997](../elliotts_casper_controller/core.py#L967-L997) |
| 🟠 | **Unvalidated config import.** Any JSON is saved, including `caspar_exe_path`, which is then executed. | [core.py:888-900](../elliotts_casper_controller/core.py#L888-L900) |
| 🟠 | **AMCP injection.** `clip` is quoted but not escaped; `"` or CR/LF adds commands. | [core.py:801](../elliotts_casper_controller/core.py#L801), [core.py:823](../elliotts_casper_controller/core.py#L823) |
| 🟠 | **Unescaped XML.** Names and paths are written with f-strings; `<`, `&` or `--` breaks `casparcg.config`. | [config_manager.py:138-176](../elliotts_casper_controller/config_manager.py#L138-L176) |
| 🟠 | **No CSRF protection; localhost bypass.** Any page open on the playout PC can drive the app. | [core.py:193-195](../elliotts_casper_controller/core.py#L193-L195) |

#### Runtime behaviour

| Sev | Finding | Where |
|---|---|---|
| 🔴 | **Kills unrelated processes.** On launch, anything with a connection on the web port is killed (including clients). Start All kills every `casparcg.exe` on the machine. | [gui_launcher.py:52-59](../elliotts_casper_controller/gui_launcher.py#L52-L59), [process_manager.py:164-179](../elliotts_casper_controller/process_manager.py#L164-L179) |
| 🟠 | **Consoles can't be viewed.** `show_console()` is never called. After a crash, `cmd /k` leaves hidden `cmd.exe` processes that nothing cleans up. | [process_manager.py:90-113](../elliotts_casper_controller/process_manager.py#L90-L113) |
| 🟠 | **Start All blocks the request** for up to N × 95 s. GUI and web can run it at once, and `_managers` has no lock. | [core.py:601-643](../elliotts_casper_controller/core.py#L601-L643) |
| 🟠 | **Saving settings while live hits the wrong process.** IDs are renumbered by list order while managers keep the old IDs. Saving also wipes each live `html_cache`. | [core.py:865-871](../elliotts_casper_controller/core.py#L865-L871), [config_manager.py:132-136](../elliotts_casper_controller/config_manager.py#L132-L136) |
| 🟠 | **Non-atomic config writes** while every request re-reads the file. A partial read gives 500s; a crash mid-write loses config. | [config_manager.py:102-104](../elliotts_casper_controller/config_manager.py#L102-L104) |
| 🟠 | **Wrong tally.** Substring matching means `PCR3 GFX1` matches `PCR3 GFX10` and same-named sources on other machines. | [ndi_tally.py:84](../elliotts_casper_controller/ndi_tally.py#L84) |
| 🟡 | **Duplicate AMCP ports** are not rejected. | [config_manager.py:39-52](../elliotts_casper_controller/config_manager.py#L39-L52) |
| 🟡 | **Slow AMCP.** Each call sleeps and waits for a 0.5 s timeout instead of parsing the reply. | [amcp_client.py:12-29](../elliotts_casper_controller/amcp_client.py#L12-L29) |
| 🟡 | **No logs.** The frozen exe sends output to `devnull`; the event log is memory only. | [__main__.py:9-13](../elliotts_casper_controller/__main__.py#L9-L13) |

#### Packaging and deployment

| Sev | Finding | Where |
|---|---|---|
| 🔴 | **Not Intune-compatible.** Per-user install to `{localappdata}` lands in SYSTEM's profile under Intune. Config is written next to the exe, which isn't writable in Program Files. | [setup.iss:24,33](../installer/setup.iss), [config_manager.py:7-10](../elliotts_casper_controller/config_manager.py#L7-L10) |
| 🔴 | **Untrusted self-update.** Downloads and runs the latest release from a personal repo with no signature check. Any version change counts, including downgrades. | [gui_launcher.py:510-624](../elliotts_casper_controller/gui_launcher.py#L510-L624) |
| 🟠 | **500 MB download at install**, from a release that only exists if CI copied the zip forward. | [setup.iss:131-175](../installer/setup.iss), [build.yml:213-237](../.github/workflows/build.yml#L213-L237) |
| 🟠 | **Unsigned binaries.** The bootloader rebuild looks like a workaround for antivirus; code signing is the fix. | [build.yml:44-61](../.github/workflows/build.yml#L44-L61) |
| 🟡 | **Wrong Python minimum.** Claims 3.8; `str \| None` hints need 3.10. | [pyproject.toml:10](../pyproject.toml#L10), [process_manager.py:310](../elliotts_casper_controller/process_manager.py#L310) |
| 🟡 | **Licensing and branding.** CasparCG is GPL-3.0; "Elliott's" and "PCR3" are hard-coded. (ITV Reem fonts stay, as this is an ITN build.) | [static/](../static/), [core.py:63](../elliotts_casper_controller/core.py#L63) |

### 3.3 Complexity (`review` pass)

```
gui_launcher.py:L641-972: shrink: four start/restart variants (start, start-remaining, restart-one, restart-all) that duplicate core.py's. One InstanceManager.start_sequence()/restart(id), ~60 lines total.
gui_launcher.py:L915-943: delete: _restart_inst, never called. Nothing replaces it.
gui_launcher.py:L907-913: delete: _send_instance_load, a copy of core._load_instance. Call the one copy.
gui_launcher.py:L811-824: delete: _sync_api_managers mirroring dicts across modules. A single owner of _managers.
gui_launcher.py:L510-634: delete: self-updater (check, download, apply, 3 status callbacks). Intune supersedence.
gui_launcher.py:L1035-1102: delete: disco-mode easter egg (+ L112-113, L217). Nothing replaces it.
gui_launcher.py:L354-393: delete: PIL pulse image re-rendered at 4x supersampling every 40 ms. Tray icon tint, or a Unicode dot in a Label.
gui_launcher.py:L154-198: native: hand-drawn Canvas buttons with manual enable/disable/redraw. ttk.Button with state=, or nothing (tray-only).
gui_launcher.py:L47-59: delete: _is_port_in_use + _kill_port. Let uvicorn's bind error surface and show it.
gui_launcher.py:L88-1109: yagni: 1,000-line Tk window once the web UI is the only UI. pystray menu (Open UI / status / Quit), ~60 lines.
gui_launcher.py:L38-44 + core.py:L1536-1543: shrink: _get_local_ip written twice. One helper.
core.py:L208-450,L1077-2118: native: ~1,150 lines of HTML/CSS/JS in Python strings with manual {{ }} escaping. Plain .html/.css/.js under web/ served by StaticFiles.
core.py:L523-528: native: hand-rolled static route. app.mount("/static", StaticFiles(directory=STATIC_DIR)), 1 line (also contains paths).
core.py:L295-296,L384-389,L443-449: delete: unused CSS (.grid-3, .grid-5, multiviewer .mv-*, table/thead). Nothing replaces it.
core.py:L646-735: shrink: stop-all, restart-one and restart-all each re-implement stop-then-sequential-start. _stop_all() + _start_sequence(insts), ~25 lines.
core.py:L777-826: shrink: /api/load and /api/instance/{id}/load are the same body twice. One _play_clip(inst, clip, loop) helper.
core.py:L154-158: stdlib: thread list + join loop per poll. ThreadPoolExecutor.map (already imported L8).
core.py:L1175-1176: delete: sourceInfo/loadUrl dead aliases. Use layerHtml directly.
core.py:L2125-2147: delete: start_server/stop_server, never called (+ webbrowser import L6). Nothing replaces it.
core.py:L878,L890: shrink: import json as _json inside functions. One top-level import.
amcp_client.py:L41-45: delete: stop_channel, info_channel, never called. Nothing replaces it.
process_manager.py:L90-113,L201-307: delete: console HWND lookup, icon injection, show/hide (never called). stdout=PIPE, ~0 lines of Win32.
process_manager.py:L56-58: delete: cmd /k color && title wrapper. Popen([exe, config]) directly.
process_manager.py:L143-179: shrink: two kill loops sharing the parent-cmd lookup. One _kill(procs); the cmd parent goes away with the wrapper.
process_manager.py:L39,L44 + config_manager.py:L17,L90-91 + core.py:L62,L541: delete: startup_delay, stored, migrated, coerced, never read (90s is hard-coded). Nothing replaces it.
config_manager.py:L33-35,L65-83: delete: migrations from the pre-multi-instance format and the misspelled filename. Fresh company installs have neither.
config_manager.py:L188-247: delete: PIL test-pattern generator; the same PNGs already ship in Lite Casper Server/media. Ship them in the CasparCG package.
ndi_tally.py:L103-107: delete: magic -10 bandwidth fallback for unknown builds. Pin ndi-python.
__main__.py:L20-27: delete: cleanup for the old exe-swap updater. Nothing replaces it.
ElliottsCasparController.spec:L23-108: native: 85-line hand-maintained hiddenimports list. pyinstaller-hooks-contrib covers FastAPI/uvicorn; collect_submodules("uvicorn") if needed.
.github/workflows/build.yml:L44-61: delete: bootloader rebuild from PyInstaller master. Stock bootloader + code signing.
.github/workflows/build.yml:L131-190 + MANIFEST.in: delete: PyPI build/publish for an internal app. Nothing replaces it.
.github/workflows/build.yml:L213-237: delete: LiteCasparServer.zip carry-forward search. Separate versioned CasparCG package.
installer/setup.iss:L84-175: delete: Pascal downloader + PowerShell extractor. CasparCG as its own Intune app / dependency.
requirements.txt: delete: duplicates pyproject deps (and adds pyinstaller). pip install .[ndi,build].
pyproject.toml:L48-55: yagni: dev extras (pytest-asyncio, black and ruff, bandit) with no tests or CI using them. ruff + pytest.
scripts/: delete: two pre-app PowerShell scripts with hard-coded Singular URLs. Superseded by the app.
update_test/: delete: test harness for an updater mechanism no longer used. Nothing replaces it.
elliotts_caspar_config.json: delete: stale config in the old "channels" format at repo root. A documented example in docs/.
```

`net: -2050 lines possible.`

> The `config_manager.py:L33-35,L65-83` cut is deferred while v1.x installs remain (see 4.12).

---

## 4. Suggestions

### 4.1 Module structure

Split `core.py` (2,150 lines) and `gui_launcher.py` (1,130 lines). Use one spelling: **Caspar**.

```
caspar_controller/
  __main__.py            # args, logging, start agent + tray
  settings.py            # pydantic Settings, %ProgramData% paths, atomic load/save
  log.py                 # rotating file log + in-memory event feed
  caspar/
    amcp.py              # persistent socket, parses reply codes
    info.py              # INFO parsing for the CasparCG version we ship
    config_xml.py        # casparcg.config via ElementTree (escapes for free)
    process.py           # Popen, stdout reader → ring buffer, exit detection
  instances.py           # InstanceManager: single owner, lock, start sequence, watchdog
  tally.py               # exact name match, local sources only
  remotes.py             # client for other agents (no generic proxy)
  api/
    app.py               # create_app(), middleware, StaticFiles
    auth.py              # API key + session dependencies
    instances.py  config.py  media.py  remotes.py
    events.py            # /api/events SSE: status, log, console
  tray.py                # status, Open UI, Restart all, Quit
  web/                   # static front end, no build step
    index.html  settings.html  triggers.html  login.html  app.css  app.js  console.js
tests/
  fake_amcp.py  test_config_xml.py  test_info.py  test_auth.py  test_instances.py
```

Rules: no module over ~400 lines, no HTML in Python, only `instances.py` touches processes.

### 4.2 API key

One static key, accepted as a header or a query parameter.

- **Generation:** `secrets.token_urlsafe(32)` on first run, stored in the `%ProgramData%` config (ACL-restricted). Shown and rotatable in Settings.
- **Accepted as:** `X-API-Key: <key>` or `?api_key=<key>`. The query form is for devices that can only send a plain GET.
- **Check:** one FastAPI dependency on every `/api/*` router, using `secrets.compare_digest`. Failure returns `401`. Only `GET /api/health` stays open.
- **Browser UI:** password login (hashed with `hashlib.scrypt`, never returned) gives an HttpOnly, SameSite=Strict cookie. Auth accepts a session or a key. Remove the localhost bypass.
- **Triggers page:** URLs include `&api_key=…`, with a header-based `curl` example alongside.
- **Remote Controllers:** store `{url, api_key}` per remote; agents send the header. Replace `/proxy` with explicit endpoints.
- **Caveats:**
  - Query keys leak into history, panel configs and proxy logs. Prefer the header and keep access logs off.
  - Plain HTTP sends the key in clear, and the galleries **aren't** isolated (4.13). Phase 2 adds a trigger-only key for Automator and an optional list of allowed client addresses; whether TLS is also needed before release is an open decision.

### 4.3 Console side panel

A slide-out panel on every page:

- **Tabs per instance** with a status dot (live, starting, crashed) and unread count. A pin option keeps it open beside the dashboard.
- **Live output:** last ~5,000 lines per instance in a `deque`, streamed over SSE. Follow/pause, search, copy and download.
- **Optional command line** to CasparCG's stdin, for signed-in operators only.
- **How:** `Popen([exe, cfg], stdout=PIPE, stderr=STDOUT, stdin=PIPE, creationflags=CREATE_NO_WINDOW)` with one reader thread per process. Strip ANSI codes. If output is buffered when not attached to a console, tail `log\inst_N\*.log` instead.
- **Gains:** removes ~130 lines of Win32 code, and crashes are detected immediately.

### 4.4 Live status over SSE

One `/api/events` stream replaces the 2 s/3 s polling. Cards update in place (removing the dropdown workaround at [core.py:1211-1215](../elliotts_casper_controller/core.py#L1211-L1215)), and there is one AMCP poller per agent however many browsers are open.

### 4.5 Process handling

- Start and stop **only processes the agent owns**. No killing by name or by any connection on a port.
- **Non-blocking jobs:** APIs return `202` and progress arrives over SSE. One lock serialises jobs.
- **Stable instance IDs** so reordering never targets the wrong process. Wipe `html_cache` only when that instance starts.
- **Persistent AMCP connection** with proper reply parsing. `VERSION` for liveness, `INFO` for clip details only.

**Auto-restart (on by default)**

- **Settings:** a global `auto_restart` toggle, default `true`, with an optional per-instance override (`on`, `off` or `inherit`). Both appear in Settings, and each instance card has a quick toggle.
- **Triggers:**
  - **Crash:** the process exits unexpectedly. The output pipe closing or `poll()` returning reports this immediately.
  - **Hang:** the process is alive but has missed `VERSION` replies for 30 s (configurable). The threshold is kept conservative so a busy instance isn't killed. No gallery output takes more than 60 s to start (4.13), so these defaults stand.
- **Restart:** start the instance again, wait for AMCP, then **re-send its startup command** (HTML URL or media command) so the output comes back with its content, not blank.
- **Back-off and cap:** wait 2 s, 5 s, 15 s, 30 s and then 60 s between attempts. After 5 restarts in 10 minutes, stop retrying, mark the instance **failed** and raise a dashboard alert. A clean run of 10 minutes resets the counter.
- **Operator intent wins:** Stop and Stop All switch auto-restart off for that instance until it is started again, so a deliberate stop is never undone. Settings changes and Restart All pause it while they run.
- **Visibility:**
  - Cards show *restarting (2/5)* or *failed*.
  - Each restart is logged with its reason and exit code, and marked in the console panel.
  - Every event goes out over SSE, so the operator is told rather than finding out from a gap in the output.

### 4.6 Config and storage

- `%ProgramData%\CasparController\config.json` and `logs\`. `CASPAR_CONTROLLER_HOME` overrides for development.
- Pydantic validation on save and import: port ranges, unique ports and names, safe characters.
- Atomic save via a temp file and `os.replace()`; cache in memory.
- Intune can deploy a `profile.json` to seed a new install with a gallery's instances.

### 4.7 Packaging and Intune

| Today | Proposed |
|---|---|
| Per-user install in `%LOCALAPPDATA%` | Per-machine in `C:\Program Files\Caspar Controller\`, silent switches, `.intunewin` |
| Installer downloads CasparCG | CasparCG and NDI Runtime as separate Intune apps, set as **dependencies** |
| Self-updater | Removed; Intune supersedence |
| Manual launch | Auto-start at logon, tray only, single-instance mutex |
| No firewall rule | Inbound rule for the web port, Domain/Private profiles |
| Unsigned | Exe and installer signed in CI; drop the bootloader rebuild |
| Personal GitHub, PyPI | Company GitHub org; CI builds `.intunewin` artefacts |

**Detection rule:** file version of `CasparController.exe` in Program Files, or an installer registry key.

### 4.8 `LiteCasparServer.zip`

**Problems:** a hand-built ~500 MB CasparCG of unknown version, git-ignored and copied between releases by CI. Users extract it by hand and type the path. Its GPL-3.0 compliance is unclear, and the unknown version is why INFO parsing (3.1 #1) can't be pinned.

**Proposal:**
1. **Build it in CI** from the official CasparCG release matching the version galleries run today (checked against its SHA-256; upgrading is a separate backlog item), adding test media, a default config and a licence notice with a source link.
2. **Install to a fixed path** (`C:\Program Files\CasparCG Server\`) with media in `%ProgramData%\CasparCG\media`.
3. **Auto-detect it** so `caspar_exe_path` becomes an override, not a setup step.
4. **Show the CasparCG version** on the dashboard and in `/api/health`.
5. **Rename it** to "CasparCG Server (ITN build)". "Lite" suggests a fork it probably isn't.

### 4.9 Quickstart

The current quickstart has around 15 steps, several out of date. Proposed README version:

> **Quick start (gallery PCs)**
>
> 1. Install **Caspar Controller** from **Company Portal**. CasparCG and the NDI Runtime come with it.
> 2. Click the **tray icon**, then **Open Caspar Controller**.
> 3. The **setup wizard** asks how many outputs you need and their names, or imports your gallery's profile.
> 4. Click **Start All**.
>
> **Triggering clips:** Settings → **Triggers** → pick instance and clip → **Copy URL** (includes the API key).

**Setup wizard:** check CasparCG and NDI → number and type of outputs → names (NDI names suggested) → show the API key → Start All. Settings becomes the advanced view.

**Developers (CONTRIBUTING.md):**
```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev,ndi]"
python -m caspar_controller --fake-caspar --no-tray
pytest
```

### 4.10 Testing and CI

**Step zero: pin current behaviour before refactoring.** Write characterisation tests against today's code so the restructure can be shown to preserve behaviour.

| Area | Test | How |
|---|---|---|
| Config generation | Known config → exact XML produced today | Golden files in `tests/golden/` |
| Config loading | Old formats migrate; missing keys backfilled | Fixture JSON → `load()` |
| Port assignment | Base + index, override, reordering | Table test on `instance_amcp_port()` |
| Start sequence | Config order, waits for AMCP, 5 s gap, startup command sent | Fake AMCP + fake `casparcg.exe`; patch `time.sleep` |
| HTTP triggers | Both load endpoints send `PLAY 1-1 "<clip>" LOOP` | `TestClient` + fake AMCP |
| Status | `/api/status` fields that Companion, remotes and the UI use | Snapshot keys and types |
| Media listing | Extensions, upper-casing, sub-folders, `TEST_` grouping | Temp folder → `/api/media` |
| Clip info | Current `_parse_layer_info` output | INFO XML captured from the CasparCG in use |

Mark tests of known-bad behaviour `xfail(reason="fixed in phase N")` so fixes surface as unexpected passes.

**Then:**
- **Unit:** XML escaping, settings validation, INFO parsing, auth matrix, clip-name escaping.
- **Integration:** `InstanceManager` against fake AMCP and a fake `casparcg.exe` for start order, crash detection, restart and console streaming.
- **CI on every PR:** `ruff`, `pytest`, a build smoke test (`--self-test`, check `/api/health`). Tagged builds are signed and publish `.intunewin` files.

### 4.11 Housekeeping

- **Rename the app to "Caspar Controller"**, dropping "Elliott's" and the mixed Casper/Caspar spelling:
  - **Everywhere users see it:** installer, Start menu, tray, web UI, browser tab and Company Portal listing.
  - **In code and files:** package `caspar_controller`, exe `CasparController.exe`, repo `caspar-controller` in the company GitHub organisation, Windows app ID `itn.caspar-controller`.
  - **Bundled CasparCG:** becomes "CasparCG Server (ITN build)" (4.8).
  - **Credit:** the original author goes in the README and the web UI's About box.
- Delete `scripts/`, `update_test/`, the root config file, `MANIFEST.in` and `requirements.txt`.
- Move "PCR3" defaults into profiles or the wizard.
- Keep the ITV Reem fonts (ITN build); add CasparCG GPL notices.
- Fix the README; add `ARCHITECTURE.md` and `API.md` (FastAPI's `/docs` provides the API reference).

### 4.12 Migrating existing installs

v1.x machines need taking over cleanly, so **keep the old config migrations** until none remain.

| Item | v1.x | v2 |
|---|---|---|
| App | `%LOCALAPPDATA%\Programs\Elliott's Caspar Controller\` | `C:\Program Files\Caspar Controller\` |
| Config | `elliotts_caspar_config.json` (or `casper` spelling) next to the exe | `%ProgramData%\CasparController\config.json` |
| Instance configs, logs, caches | Beside `casparcg.exe` | Regenerated; safe to leave |
| CasparCG | Wherever the zip was extracted (default `C:\CasparCG`) | `C:\Program Files\CasparCG Server\` |
| Media | `<caspar dir>\media` or `media_path` | Unchanged |

**On first start, the v2 agent:**
1. Finds the old config in each user's `%LOCALAPPDATA%` install and `C:\CasparCG\`. If there are several, it uses the newest and logs the rest.
2. Converts it through `Settings`: applies the format migrations, assigns stable IDs (keeping `legacy_id`), hashes the password, generates an API key, turns auto-restart on and drops dead keys. It keeps `caspar_exe_path` only if the file exists.
3. Writes the new config atomically and keeps `config.v1-backup.json`. The original is never deleted.
4. Keeps `/api/load` URLs unchanged apart from the key, with an optional, off-by-default allow-list for key-less triggers during the changeover.
5. Shows a one-off dashboard notice: what was imported, the new API key and the panels that need updating.
6. An Intune remediation script then uninstalls v1.x, once step 3 has succeeded (detected by `"migrated_from": "1.x"` in the config).

**Test** with real v1.x configs collected from gallery machines, run against a fake `%LOCALAPPDATA%` tree. **Remove** the migration code once Intune shows no v1.x installs.

Only PCR3 runs v1.x (4.13). The streaming VMs are new installs, but may already run CasparCG without the controller.

### 4.13 Gallery requirements (team answers, 2026-11-05)

**Sites.** The app runs in PCR3 today. The first rollout is PCR3 plus the Streaming & Syndication VMs; PCR4 comes later. Host names and addresses are kept out of this public repo and go in the Intune site profiles (4.6).

| Site | App runs on | Operators open the UI from | HTTP control |
|---|---|---|---|
| PCR3 | The gallery's graphics server PC | A separate operator PC in the gallery | Automator |
| Streaming | Four VMs, one controller each | The same VM | One shared Automator |
| PCR4 (later) | Two VMs | Not yet known | Not yet known |

**What this settles:**
- **Usage:** HTTP triggering and the web UI from another PC are both in use. Nobody objected to Remote Controllers, so all three stay.
- **Start-up times:** no output takes more than 60 s to start, so the auto-restart defaults in 4.5 stand.
- **Network:** not isolated. PCR3 can be reached from anywhere on the corporate network. That moves three things forward:
  - a trigger-only key and an optional list of allowed client addresses, so Automator's key can only fire clips and only from Automator (Phase 2)
  - sign-in rate limiting (Phase 3)
  - a firewall rule scoped to each site's operator PC and Automator (Phase 3)

  TLS stays optional, pending a decision.

- **Automator** calls plain URLs, so it sends its key as `?api_key=`. The allowed-address list stops a key copied from a URL being used from another machine.

**Worked out without asking the team:**
- **Number of streaming VMs:** the request named VMs 03 to 07, but listed 03 to 06. The Intune device group settles this at rollout.
- **CasparCG versions:** each site's version, and whether the streaming VMs already run CasparCG, are checked on the machines when the CasparCG package work starts.

### 4.14 CasparCG templates (team request, 2026-11-05)

The team wants graphics played from CasparCG templates directly, instead of loading an external graphics app (Singular, SPX) into an HTML output.

- **New instance type, "Graphics (templates)":** starts blank, same NDI output and start sequence as the other types.
- **Explicit CG routes**, not raw AMCP: list templates, then add, play, update, next, stop and clear on a layer, with field data. Each command also has a GET form for Automator, and the trigger-only key accepts it.
- **Template data is escaped for AMCP.** Today's clip checks reject the quotes that template data needs.
- **Template folder** comes from the CasparCG package (`%ProgramData%\CasparCG\templates`) and is written into each instance's config. Today it is hard-coded as `template\` next to the exe.
- **Operators** get a template panel on graphics cards; the Triggers page builds template URLs.

**Before starting:** ask the team for one or two sample templates. They show the format, the field names and the data Automator sends; Flash templates don't run on CasparCG 2.3 and later. Unless the samples say otherwise, assume:
- HTML templates.
- Any layer.
- Field data is optional.
- Templates are copied into the folder by hand until Intune or a share is set up.

---

## 5. Roadmap

| Phase | Outcome | Contents |
|---|---|---|
| **0. Stop the bleeding** | Tested and safe on a network | Characterisation tests first, then auth on all routes, `StaticFiles`, drop `cmd /k`, hide password, disable updater |
| **1. Restructure** | One owner of state | Module split, `InstanceManager`, settings in `%ProgramData%` |
| **2. Web UI** | Slick operator experience | Static front end, SSE, console panel, auto-restart, Triggers page, explicit remote API, trigger-only key, CasparCG templates |
| **3. Deployment** | One-click Company Portal install | Signed per-machine installer, Intune dependencies, v1.x migration, wizard, quickstart, sign-in rate limit |
| **4. Polish** | Supportable long-term | File logs, health endpoint, docs |
| **Backlog** | Only if needed | TLS, central dashboard, CasparCG version upgrade |

Each phase ends in a tagged release. A task is done when its **Done when** check passes.

### Phase 0: Stop the bleeding

- [ ] **Test safety net, before any other change:** `tests/`, `pytest` in CI, `fake_amcp.py`, and the characterisation suite (4.10) on unchanged code. *Done when:* `pytest` runs on every PR, and every 4.10 row has a passing or xfail test that keeps passing through the refactor.
- [ ] **API key and session auth** on all `/api/*`; remove localhost bypass. *Done when:* auth-matrix test passes for every route except `/api/health`.
- [ ] **`StaticFiles` mount.** *Done when:* `/static/C:/Windows/win.ini` and `/static/../pyproject.toml` return 404.
- [ ] **Launch CasparCG without `cmd /k`.** *Done when:* an instance named `A | B & C <D>` starts and the process tree is only `casparcg.exe`.
- [ ] **Hash the password; never return it.** *Done when:* `/api/config` contains no `web_password`.
- [ ] **Disable the self-updater.** *Done when:* no requests to `api.github.com`.
- [ ] **Escape generated XML and UI text.** *Done when:* names containing `<&"--` give valid XML and render as text.

### Phase 1: Restructure

- [ ] **New package layout** (4.1), moving code before changing it. *Done when:* tests pass, `core.py` and `gui_launcher.py` are gone, no module over ~400 lines.
- [ ] **`InstanceManager` as single owner**; remove `_sync_api_managers` and GUI duplicates. *Done when:* only `instances.py` constructs processes, and concurrent Start All runs once.
- [ ] **Non-blocking start/restart.** *Done when:* `POST /api/server/start` returns in under 200 ms.
- [ ] **Stable instance IDs.** *Done when:* reordering while live still stops the right process.
- [ ] **Kill only owned processes.** *Done when:* an unrelated `casparcg.exe` and a connected browser survive Start All and app launch.
- [ ] **Pydantic settings, atomic save.** *Done when:* duplicate ports, names and unsafe characters are rejected, and reads never see partial JSON.
- [ ] **New AMCP client.** *Done when:* reply-code and multi-line tests pass, and a local call takes under 50 ms.
- [ ] **Apply the 3.3 cuts** in moved code. *Done when:* each line is removed or marked deferred.

### Phase 2: Web UI

- [ ] **Static pages in `web/`.** *Done when:* no HTML strings remain in Python.
- [ ] **SSE events**; remove polling. *Done when:* changes appear in under 1 s and open dropdowns aren't reset.
- [ ] **Console panel** (4.3). *Done when:* output streams live and a crashed instance shows *crashed* with its last lines.
- [ ] **Auto-restart** (4.5), default on, global and per-instance toggles. *Done when:* a killed or hung fake instance restarts with its startup command re-sent; repeated crashes stop at the cap with an alert; an operator Stop is never undone.
- [ ] **Triggers page.** *Done when:* a copied URL works from `curl` on another machine.
- [ ] **Explicit remote API** with per-remote keys; delete `/proxy` and `/remote/test`. *Done when:* two agents control each other in a test, and no endpoint accepts an arbitrary URL.
- [ ] **Fix INFO and clip parsing** (3.1 #1, #2, #8, #9). *Done when:* fixture tests pass and remote cards show clip and time.
- [ ] **Trigger-only key and allowed client addresses** (4.2, 4.13). *Done when:* the trigger key can call `/api/load` but gets 403 on `/api/config`, and key calls from unlisted addresses are refused.
- [ ] **CasparCG template outputs** (4.14). *Done when:* a template with escaped field data plays from a Triggers-page URL and from the dashboard.

### Phase 3: Deployment

- [ ] **CasparCG package from CI** (4.8). *Done when:* CI outputs a versioned `.intunewin` with SHA-256 and licence notice.
- [ ] **Signed per-machine installer**; downloader and updater removed. *Done when:* a silent SYSTEM install lands in Program Files, signatures verify, uninstall is clean.
- [ ] **Intune apps, dependencies, detection, supersedence.** *Done when:* a clean machine installs from Company Portal with no manual steps.
- [ ] **Tray auto-start, mutex, firewall rule** scoped per site (4.13). *Done when:* the tray appears at sign-in, a second launch focuses the first, and remote browsers connect.
- [ ] **Rate-limit sign-in, expire sessions.** *Done when:* repeated wrong passwords from one address are refused, and old sessions are rejected.
- [ ] **v1.x migration** (4.12). *Done when:* fixture tests pass and a real v1.x machine keeps its config, media and triggers, with v1.x removed.
- [ ] **Wizard and new README.** *Done when:* a new user reaches live NDI outputs from the README alone.
- [ ] **Rename to "Caspar Controller"** (4.11) across installer, exe, tray, web UI and repo. *Done when:* "Elliott's" and "Casper" appear nowhere except the author credit.

### Phase 4: Polish

- [ ] **Rotating file logs** and a diagnostics zip. *Done when:* logs survive restarts and the zip redacts secrets.
- [ ] **`/api/health`.** *Done when:* monitoring can read it without auth and it exposes nothing sensitive.
- [ ] **Docs and licensing** (4.11). *Done when:* docs reviewed and CasparCG GPL notices included.

### Backlog

- [ ] **Optional TLS.** Decide before company release whether it's required (4.13). *Done when:* the UI and API work over HTTPS with the configured certificate.
- [ ] **Central dashboard** (option B) on top of the agent API, if one URL for all galleries is wanted.
- [ ] **Upgrade CasparCG** to a newer release, done separately from the Phase 3 packaging, which keeps the version galleries run today.
