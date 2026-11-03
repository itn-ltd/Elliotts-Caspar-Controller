# Caspar Controller

Windows app that runs several CasparCG servers on one PC, one NDI output each, controlled from a web UI and an HTTP API (Companion, hardware panels). Being refactored for company-wide release via Intune.

The plan is `docs/REVIEW.md`; the work is tracked as GitHub issues labelled `phase-0` to `phase-4` and `backlog`.

## Workflow

- Branch from `feature/v2-refactor`, PR back into it. Never PR into `master`.
- One issue per branch/PR. Do tickets in order; #1 (tests) comes first.
- GitHub CLI defaults to the original author's repo: always pass `-R itn-ltd/Elliotts-Caspar-Controller`.
- `build.yml` builds and releases on version tags only. Don't push tags.

## Commands

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
pytest                          # full suite, ~1.5 min (Windows: launches real processes)
ruff check . && ruff format .   # lint + format (ruff is the only formatter)
pre-commit install              # optional: same checks on every commit
```

CI (`.github/workflows/ci.yml`) runs Lint (Linux) and Test (Windows) on every PR.

## Code quality

Use the `code-quality` skill (`.claude/skills/code-quality/`) when writing or reviewing code. In short: don't repeat yourself, reuse shared helpers and the standard library, keep comments short.

## Tests

- `tests/` pins today's behaviour. If a change breaks a characterisation test, either the change is wrong or the test must be updated on purpose in the same PR.
- `tests/test_known_issues.py` holds strict `xfail` tests for known bugs, each tagged with its issue. When you fix one, remove its marker.
- Use the existing fakes (`fake_amcp.py`, `fake_caspar.py`) and `conftest.py` fixtures; don't write new ad hoc ones.
- Never call the real "kill every CasparCG" helpers outside the stubs in `conftest.py`.
- Claude's shell sets `NoDefaultCurrentDirectoryInExePath=1`, which stops today's launcher finding `casparcg.exe` (#3). The `caspar_dir` fixture clears it.

## Gotchas

- `core.py` and `gui_launcher.py` are being split up (#4). Until then, the GUI and web server each have their own start/stop code: change both or neither.
- `noqa` comments in the app code mark known bugs and name the ticket that fixes them. Fix the bug in that ticket; don't just delete the `noqa`.
- Package name is still `elliotts_casper_controller` (Casper spelling) until #4/#13.
- The repo is public until it's moved to a private one. Don't commit secrets, real URLs or gallery configs.
