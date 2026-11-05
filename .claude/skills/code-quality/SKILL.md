---
name: code-quality
description: Code quality rules for Caspar Controller. Use whenever writing, changing, refactoring or reviewing Python, JS or tests in this repo, and before committing or opening a PR. Covers don't-repeat-yourself, reusing shared helpers and the standard library, short comments, error handling and tests.
---

# Code quality

Apply these to every change. Check your diff against the checklist at the end before calling the work done.

## 1. Don't repeat yourself

- **Search before you write.** `grep` for an existing function, constant or fixture that already does it. Reuse or extend it.
- **One implementation per behaviour.** If logic appears twice, extract it into a shared function in the module that owns the concept. Don't copy it with small changes.
- **One source of truth for values.** Ports, paths, default config, version numbers and the empty-layer dict (`caspar.info.EMPTY_LAYER`) live in one place and are imported.
- **Same in tests:** use `conftest.py` fixtures, `instance()`, `fake_amcp.py` and `fake_caspar.py`. Add a fixture instead of repeating setup.

Don't add a second copy of anything that already has one home, e.g. start/stop/restart (`InstanceManager`) or AMCP connections (`instances.manager.client()`).

## 2. Where shared code goes

1. **Standard library or an existing dependency first.** Examples for this app:
   - `StaticFiles` for static routes
   - `ThreadPoolExecutor.map` for parallel polling
   - `xml.etree.ElementTree` or `xml.sax.saxutils.escape` for XML
   - `secrets.compare_digest` for keys
   - `os.replace` for atomic writes
   - pydantic for validation
   - `pathlib` for paths
2. **An existing project helper** (search first).
3. **A new function in the module that owns the concept:** AMCP in the AMCP client, config in the config/settings module, process handling in the process module.
4. **`utils.py`** only for small, pure, genuinely cross-cutting helpers. It is not a dumping ground; if a helper belongs to a concept, put it with that concept.

Don't add a dependency for something a few lines or the standard library can do. If you do add one, say why in the PR.

## 3. Comments

- **Comment why, not what.** If the code needs a comment to say what it does, rename or simplify it first.
- **One line where possible.** Two or three lines only for a genuinely non-obvious reason.
- **Docstrings:** a one-line summary for public functions and classes. Only add more for real caveats.
- **No:** narration of obvious code, commented-out code, changelog notes ("v1.0.8: fixed ..."), or banner comments repeating the function name.
- **Ticket references are short:** `# Known race, fixed in #5`.

```python
# Bad
# Loop through each instance in the list of instances and check whether
# it is running by sending a ping, then store the result in the dict
for inst in instances:
    live[inst["id"]] = client.ping()

# Good (no comment needed)
for inst in instances:
    live[inst["id"]] = client.ping()

# Good (explains a non-obvious why)
time.sleep(5)  # receivers latch onto the wrong NDI source if outputs start together
```

## 4. Structure and naming

- Functions do one thing. Split anything you can't describe in one short sentence.
- Modules stay under about 400 lines. No HTML/CSS/JS inside Python strings: pages live in `caspar_controller/web/` and get their data from the API.
- Descriptive names. No abbreviations beyond the domain terms: AMCP, NDI, inst, cfg.
- Type hints on new or changed function signatures.
- No dead code: unused parameters, flags nobody sets, unreachable branches.

## 5. Errors and safety

- No silent `except Exception: pass`. Catch what you expect; log anything you swallow.
- `raise ... from err` (or `from None`) inside `except`.
- Never use the `except` variable inside a deferred callback; bind it first (`msg = str(exc)`).
- No shell strings for processes: `subprocess.Popen([exe, arg])`.
- Validate input at the API boundary; escape output (XML, HTML, AMCP).

## 6. Tests

- Every behaviour change comes with a test in the same PR.
- A failing characterisation test means the change is wrong, or the test is updated deliberately and the PR says why.
- When you fix a bug pinned in `tests/test_known_issues.py`, remove its `xfail` marker.

## Checklist

- [ ] Searched for existing code before writing new code; no new duplication
- [ ] Standard library / existing helper used where one exists
- [ ] Comments are short and explain why; no commented-out code
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `pytest` passes; new behaviour has a test
