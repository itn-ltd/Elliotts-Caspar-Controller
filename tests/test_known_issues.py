"""Known-bad behaviour, pinned as expected failures.

Each test asserts the *correct* behaviour and is marked ``xfail`` with the issue
that fixes it. xfail is strict (pyproject.toml), so when a fix lands the test
"unexpectedly passes" and fails the run: remove the marker in that PR.

None are open: the #12 migration tests moved to test_v1_migration.py.
"""

import pytest


def issue(number: int, why: str, raises: type | tuple = AssertionError) -> pytest.MarkDecorator:
    # `raises` stops an unrelated error (e.g. from a partial fix) passing as "still broken".
    return pytest.mark.xfail(reason=f"#{number}: {why}", raises=raises)
