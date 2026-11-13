"""One pytest test per landmine case in run_tests.CASES (the test ID is the case name).
    pytest tests/test_landmines.py -k challenge-word-on-rich-page
Cases that need a real browser are skipped (not failed) when Playwright/Chromium cannot start.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_tests  # noqa: E402


@pytest.fixture(scope="module")
def landmine_server():
    srv = run_tests.start_server()
    yield
    srv.shutdown()


@pytest.mark.parametrize("name", list(run_tests.CASES), ids=list(run_tests.CASES))
def test_landmine(name, landmine_server, tmp_path):
    if run_tests.CASES[name][1] != "never" and not run_tests.browser_available():
        pytest.skip("Playwright/Chromium is not available")
    err = run_tests.run_case(name, tmp_path)
    assert err is None, err
