# Contributing to Caspar Controller

## Set up

Python 3.10+ on Windows.

```bash
git clone https://github.com/itn-ltd/Elliotts-Caspar-Controller.git
cd Elliotts-Caspar-Controller
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev,ndi]"     # ndi is optional: it adds NDI tally
pre-commit install               # optional: lint and format on every commit
```

## Run it without a real CasparCG

```bash
python tools/preview.py          # http://127.0.0.1:5390, opens your browser
```

This runs the app against fake CasparCG servers (`tests/fake_amcp.py`) with a throwaway config in `.home/preview`. You get live and stopped outputs, a clip playing and console lines to work on. Nothing touches `%ProgramData%` or a real CasparCG.

To run the real tray app from source, point it at its own config folder first:

```bash
set CASPAR_CONTROLLER_HOME=%CD%\.home\dev
python -m caspar_controller
```

> **Don't run it against a gallery's real config.** With auto-start on, Start All restarts CasparCG and takes outputs off air.

## Test and lint

```bash
pytest                           # full suite, about 3 min: it launches fake CasparCG processes
ruff check . && ruff format .    # ruff is the only linter and formatter
```

- **Characterisation tests:** `tests/` pins today's behaviour. If a change breaks a test, either the change is wrong or the test is updated on purpose in the same PR, and the PR says why.
- **Known bugs:** `tests/test_known_issues.py` holds strict `xfail` tests, each tagged with the issue that fixes it. Remove the marker in that issue's PR.
- **Fixtures:** use `conftest.py`'s fixtures, `fake_amcp.py` and `fake_caspar.py`, rather than writing new ad hoc ones. Tests may only kill processes they launched, and should use `free_port()`, never 5250.

## Workflow

- **Branches:** one issue per branch and PR. Branch from `feature/v2-refactor` and open the PR back into it, never into `master`.
- **Issues:** the plan is [`docs/REVIEW.md`](docs/REVIEW.md), and the work is the issues labelled `phase-0` to `phase-4` and `backlog`.
- **Code style:** see `.claude/skills/code-quality/SKILL.md`. Don't repeat yourself, reuse the standard library and existing helpers, and keep comments short and about *why*.
- **Public repo:** this repository is public. Never commit secrets, real host names or addresses, or gallery configs. Site profiles live in Intune.
- **Releases:** don't push version tags. `build.yml` builds and releases on tags only.
