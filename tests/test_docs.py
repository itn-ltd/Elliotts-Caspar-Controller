"""docs/API.md matches the app's endpoints (#16)."""

import importlib.util

from conftest import TESTS_DIR

TOOL = TESTS_DIR.parent / "tools" / "api_docs.py"


def test_api_md_is_up_to_date():
    spec = importlib.util.spec_from_file_location("api_docs", TOOL)
    api_docs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api_docs)
    current = api_docs.OUT.read_text(encoding="utf-8")
    assert current == api_docs.render(), "docs/API.md is stale: run python tools/api_docs.py"
