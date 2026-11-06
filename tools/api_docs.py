"""Write docs/API.md from the app's OpenAPI schema: python tools/api_docs.py

tests/test_docs.py fails when API.md is out of date, so run this after changing an endpoint.
Summaries are each endpoint's docstring; who may call it comes from the auth middleware's rules.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from caspar_controller.api import auth  # noqa: E402
from caspar_controller.api.app import app  # noqa: E402

OUT = REPO / "docs" / "API.md"

INTRO = """# Caspar Controller HTTP API

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
"""


def access(path: str) -> str:
    if path in auth.OPEN_PATHS:
        return "Open"
    if path in auth._SESSION_ONLY_PATHS or not path.startswith("/api/"):
        return "Session"
    if auth._TRIGGER_PATH.fullmatch(re.sub(r"\{[^}]+\}", "1", path)):
        return "Trigger"
    return "Full key"


def _type(schema: dict) -> str:
    if "enum" in schema:
        return " or ".join(f"`{v}`" for v in schema["enum"])
    if "anyOf" in schema:
        return " or ".join(_type(s) for s in schema["anyOf"] if s.get("type") != "null")
    return schema.get("type", "any")


def parameters(op: dict, schemas: dict) -> str:
    parts = []
    for p in op.get("parameters", []):
        s = p.get("schema", {})
        if p["in"] == "path" and "enum" not in s:
            continue  # already in the path
        default = f", default `{str(s['default']).lower()}`" if "default" in s else ""
        parts.append(f"`{p['name']}` ({_type(s)}{default})")
    body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    if body and "$ref" in body:
        fields = schemas[body["$ref"].rsplit("/", 1)[1]].get("properties", {})
        parts.append("JSON: " + ", ".join(f"`{k}`" for k in fields))
    elif body:
        parts.append("JSON object")
    return "; ".join(parts)


def render() -> str:
    spec = app.openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    rows = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            summary = (op.get("description") or op["summary"]).strip().splitlines()[0]
            cells = [method.upper(), f"`{path}`", access(path), summary, parameters(op, schemas)]
            rows.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return INTRO + "\n".join(rows) + "\n"


if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUT.relative_to(REPO)}")
