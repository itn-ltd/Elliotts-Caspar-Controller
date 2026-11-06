# Caspar Controller

Run several CasparCG outputs from one Windows PC, each sent out as its own NDI source, and control them from a web page or over HTTP.

[![CI](https://github.com/itn-ltd/Elliotts-Caspar-Controller/actions/workflows/ci.yml/badge.svg)](https://github.com/itn-ltd/Elliotts-Caspar-Controller/actions/workflows/ci.yml)
[![Licence: MIT](https://img.shields.io/badge/Licence-MIT-yellow.svg)](LICENSE)

ITN's build of [Elliott's Caspar Controller](https://github.com/BlueElliott/Elliotts-Caspar-Controller), created by BlueElliott.

## What it does

- **One CasparCG process per output,** each with its own AMCP port and NDI name, for HTML graphics (such as Singular.live) or media clips.
- **A dashboard** showing each output's state, the clip playing and the time left, with Start, Stop, a clip picker and an AMCP command box.
- **Tally:** a card is outlined red when the vision mixer has it on programme and amber on preview.
- **Clip triggers over HTTP** for Automator, Companion and hardware panels. The Triggers page builds the URLs.
- **Auto-restart** for outputs that crash or hang, but never one that's on air.
- **Remote Controllers:** watch and control other gallery PCs from one dashboard.
- **Runs from the system tray.** Quitting the app leaves CasparCG running, so outputs stay on air.

## Requirements

- Windows 10 or 11
- CasparCG Server
- The [NDI Runtime](https://ndi.video/tools/)

## Install

Install with `CasparControllerSetup.exe`, then open **Caspar Controller** from the Start menu. It will be in Company Portal once Intune deployment is ready (#39).

The installer's optional CasparCG download still points at the original author's releases, so install CasparCG yourself for now (#10 replaces it). The app never updates itself.

## Quick start

1. **Open it:** click the tray icon, then **Open Caspar Controller**. On a new install the setup wizard opens by itself. You can open it again later from **Settings → Setup wizard**.
2. **Follow the wizard:**
   1. **CasparCG:** the path to `casparcg.exe`, and a check that the NDI Runtime is installed.
   2. **Your outputs:** how many, then each one's type (HTML or Media), name, NDI name and graphics URL. NDI names are suggested from the controller's name.
   3. **Keys:** the trigger key for Automator and panels, and the full API key for Companion and other controllers.
   4. **Start the outputs.**
   5. **Set a password,** so other PCs can use the web page.
3. **Check the dashboard:** each output's card turns green when it's live.

From another PC, browse to `http://<controller PC>:5280` and sign in.

## Upgrading from the original version (v1.x)

### Automatic import

The first time the new version starts, it looks for a v1.x config and imports it. It checks each user's v1.x install, `C:\CasparCG`, and the app's own folder, and uses the newest file it finds.

- **What carries over:**
  - every setting
  - the outputs, with the same numbers and AMCP ports
  - the media folder
  - the CasparCG path, if that file still exists
  - the password, if one was switched on
- **Auto-restart** is turned on.
- **The v1.x file isn't changed.** A copy is saved as `config.v1-backup.json` beside the new config, without the plaintext password.
- **A notice on the dashboard** says what was imported and shows the trigger key.

### Export and import by hand

Use this to move settings from a v1.x PC to a new one:

1. **On the v1.x PC:** **Settings → Config Backup → Export Config** downloads `elliotts_caspar_config.json`.
2. **On the new PC:** **Settings → General → Config Backup → Import Config**, and choose that file.

The import keeps the new PC's own password, API keys, allowed addresses and saved Remote Controller keys. It never imports the v1.x password. If the file's CasparCG path doesn't exist on the new PC, the new PC's path is kept.

### After upgrading

- **Triggers now need a key.** Add `&api_key=<trigger key>` to every Automator, Companion and panel URL, or copy new URLs from the Triggers page.
- **Changing over gradually:** under **Settings → Security → API Keys → Changeover**, list the addresses of devices that haven't been updated yet. They can fire triggers without a key for now, and each trigger is logged. Clear the list once they all send the key.
- **Removing v1.x:** run `installer\remove-v1.ps1` as an administrator, with `-WhatIf` first to see what it will do. It only removes v1.x once the new version has imported its config, and CasparCG keeps running.

## Using it

### Output cards

| Status box | Means |
|---|---|
| Green, **Live** | Running and answering |
| Plain, **Stopped** | Not running: **Start** it |
| Amber, **Crashed (1/5)**, **Hung (1/5)** or **Restarting (1/5)** | Auto-restart is bringing it back |
| Red, **Failed** | It failed 5 times in 10 minutes, so auto-restart gave up. Check its console, then **Start** it |

- **Start All** starts every output that isn't running, one every 5 seconds, so receivers pick up the right NDI source. Outputs already live aren't touched.
- **Console**, at the bottom of each card, shows that output's CasparCG log, live.
- **Auto Restart**, on each card, turns auto-restart on or off for that output.

### Triggering clips

On the **Triggers** page, pick a media output and a clip, then copy the URL:

```
http://<controller PC>:5280/api/load?instance=<output>&clip=<clip>&api_key=<trigger key>
```

- **Looping:** clips loop by default. Add `&loop=false` to play once.
- **Companion:** use a **Generic HTTP GET** action with the URL. To keep the key out of the URL, send it as an `X-API-Key` header instead.
- **Automator:** calls the same URL.

### Remote Controllers

Add other controllers in **Settings → Remote Controllers**, each with its URL (`http://<PC>:5280`) and its own full API key. **Test** checks that it answers and accepts the key. Its outputs then appear on this dashboard.

## Security

- **Password:** until one is set, only a browser on the controller PC itself can use the web page. Set or change it in **Settings → Security**. Changing it signs out every other browser. After 5 wrong passwords in a minute, that address has to wait a minute.
- **API keys,** in **Settings → Security → API Keys:**
  - **Trigger key:** fires clips and nothing else. Give it to Automator and panels.
  - **Full API key:** everything except the password and the keys themselves. For Companion setups that start and stop outputs, and for other controllers.
  - **Sending a key:** as an `X-API-Key` header or `?api_key=` in the URL.
  - **Rotating a key** stops the old one working straight away.
- **Allowed addresses** (optional): keys then only work from the listed IP addresses, subnets or host names. Signed-in browsers aren't affected.
- **A starting password for new installs,** for example set by Intune:

  ```powershell
  [Environment]::SetEnvironmentVariable("CASPAR_CONTROLLER_INITIAL_PASSWORD", "<password>", "Machine")
  ```

  It only applies when no password is set, and the first person to sign in must choose a new one.
- **Forgotten password:** quit the app, set `"web_password_hash": ""` and `"password_change_required": false` in the config file, then start the app again. It's then back to first-run mode, so set a new password from the controller PC.

## Settings, logs and diagnostics

Everything lives in `%ProgramData%\CasparController\`:

| File | Holds |
|---|---|
| `config.json` | All settings, including the password hash and keys, so treat it as sensitive. Edit it in Settings, or by hand only while the app isn't running |
| `logs\events.log` | The dashboard's event log, kept across restarts |
| `logs\agent.log` | The app's own log, including errors |
| `profile.json` | Optional settings for a new install, for example deployed by Intune |

- **Backups:** **Settings → General → Config Backup** exports and imports the settings. Exports never include the password or any key.
- **Diagnostics:** **Settings → General → Download diagnostics** saves a zip for the service desk. It holds the settings without passwords or keys, the versions, the app's logs and each output's CasparCG log.
- **CasparCG's own files:** each output's config and logs are kept next to `casparcg.exe`. The configs are rewritten on every start, so don't edit them.

## More documentation

- [docs/RUNBOOK.md](docs/RUNBOOK.md): for the service desk, covering starting and stopping, the console, diagnostics and common problems.
- [docs/API.md](docs/API.md): every HTTP endpoint and who may call it.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how the app is put together.
- [CONTRIBUTING.md](CONTRIBUTING.md): developing, testing and the fake CasparCG preview.

## Building

```bash
pip install -e ".[build]"
pyinstaller CasparController.spec                # dist\CasparController\
iscc /DMyAppVersion="1.2.3" installer\setup.iss  # installer\Output\CasparControllerSetup.exe
```

Pushing a `v*.*.*` tag builds and publishes a release (`build.yml`).

## Licences

- **Caspar Controller:** MIT ([LICENSE](LICENSE)), from BlueElliott's original project.
- **CasparCG Server:** GPL-3.0, by the CasparCG project. Its source code is at https://github.com/CasparCG/server. The controller runs it unchanged as a separate program. The notice is also in **Settings → General → About**.
- **ITV Reem fonts:** included for ITN's build.

## Issues

https://github.com/itn-ltd/Elliotts-Caspar-Controller/issues
