# Caspar Controller: service desk runbook

For first-line support when a gallery reports a graphics or playout output problem. You don't
need to know CasparCG: everything here is done from the Caspar Controller web page.

**Before you touch anything, ask whether the output is on air.** Stopping or restarting an
output takes it off air. If it's live in a programme, agree a moment with the gallery first.

## Opening Caspar Controller

- **On the playout PC:** double-click the Caspar Controller icon in the system tray (bottom
  right, near the clock). The dashboard opens in the browser.
- **From another PC:** browse to `http://<playout PC name>:5280`. If it asks for a password,
  the gallery has it. It's the same for every output on that PC.
- **No tray icon?** Start **Caspar Controller** from the Start menu. Starting it never
  interrupts outputs that are already running.

## Reading the dashboard

Each output has a card:

| Status box | Means | Do |
|---|---|---|
| Green, **Live** | Running and answering | Nothing |
| Plain, **Stopped** | Not running | **Start**, if it should be on |
| Amber, **Crashed (1/5)**, **Hung (1/5)**, **Restarting (1/5)** | It failed; the app is restarting it by itself | Wait a minute. It usually comes back on its own |
| Red, **Failed** | It failed 5 times in 10 minutes and the app has stopped trying | Check the console (below), then **Start** |

- A red **LIVE** badge on a card means the vision mixer has that output **on program**. An amber
  **PVW** badge means it's on preview.
- The **Auto Restart** switch on each card should be on. Leave it on unless the gallery asks.

## Starting and stopping

- **Start All** (top of the dashboard) starts every output that isn't running, one every 5
  seconds, so receivers pick up the right NDI source. Outputs already live aren't touched.
- **Start** or **Stop** on a card does just that output.
- **Stop All** stops every output. Only use it when the gallery agrees.
- **Restart all instances** in the tray icon's menu restarts every output.
- Starting takes a few seconds. A card that isn't live after 2 minutes shows a warning: check
  its console.

## Reading the console

**Console** at the bottom of a card opens that output's CasparCG log, live.

- **Search:** use the search box to find `error` or `warning`.
- **Copy** or **Download:** attach the log to the ticket.
- **What to look for:**
  - Lines just before a crash.
  - `html` errors, which mean the graphics page didn't load. Check the PC can reach the
    graphics URL.
  - `ndi` errors, which mean an NDI problem. Check the NDI Runtime is installed.
- An empty console means the output hasn't started since the PC booted, or Windows isn't letting
  the app read the log.

## Downloading diagnostics

**Settings → General → Download diagnostics** saves one zip. Attach it to the ticket before
escalating. It holds:

- the configuration, with **no passwords or API keys**, so it's safe to attach
- app and CasparCG versions, and each output's state
- the event log and the app's own log, which survive restarts
- each output's recent CasparCG log

## Common problems

| Problem | Check |
|---|---|
| The page doesn't open from another PC | Does it open on the playout PC itself (`http://127.0.0.1:5280`)? If so, it's the network or the firewall, not the app |
| "Too many wrong passwords" | Wait a minute; it's per address. Check that the right password is being used |
| Clips don't fire from Automator or Companion | Their URLs need the trigger key (`&api_key=...`), shown in **Settings → API Keys** and on the **Triggers** page. If **Allowed addresses** is filled in, the device's address must be in it |
| One output keeps failing | Its console, then diagnostics. Escalate with both |
| "Setup Required" on the dashboard | CasparCG's path or the outputs aren't set: escalate to the team that installs it |
| The tray says the web server didn't start | Something else is using port 5280. Escalate |

## Escalating

Send the playout PC's name, which outputs are affected, the time it started, what you tried, and
the **diagnostics zip**.
