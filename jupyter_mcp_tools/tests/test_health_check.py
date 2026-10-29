"""Tests for scripts/health_check.py.

Worth testing despite being a dev script: it's what tells an operator
"the deployment works", so a false OK is worse than no check at all. The
failure modes below are the ones a real outage produces (nothing
listening, a gate that stopped gating), not mocked-away details.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "health_check.py"


@pytest.fixture(scope="module")
def health():
    spec = importlib.util.spec_from_file_location("health_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["health_check"] = module
    spec.loader.exec_module(module)
    return module


def test_report_counts_failures(health, capsys):
    report = health.Report()
    report.ok("fine")
    report.warn("meh")
    assert report.failed == 0
    report.fail("broken")
    assert report.failed == 1
    out = capsys.readouterr().out
    assert "[FAIL] broken" in out


def test_dead_admin_ui_fails_with_a_readable_reason(health, capsys):
    report = health.Report()
    # Port 1 requires root and nothing listens there: connection refused.
    health.check_admin_ui(report, "http://127.0.0.1:1")
    out = capsys.readouterr().out
    assert report.failed >= 1
    # Regression: a URLError used to escape as an unhandled traceback, so
    # the operator saw a Python stack instead of "is the process running?".
    assert "is the process running?" in out
    assert "Traceback" not in out


def test_open_gate_is_reported_as_a_failure(health, capsys, tmp_path):
    """A page that renders for anonymous visitors is a *failure*, not an OK.

    This is the check that makes the script worth running: the app "works"
    perfectly while being open to anyone who can reach the port.
    """

    class FakeResponse:
        status = 200

        def read(self):
            return b"<html>secret</html>"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    import urllib.request

    class FakeOpener:
        def open(self, request, timeout=None):
            url = request.full_url
            if url.endswith("/login"):
                return FakeResponse()
            return FakeResponse()  # every page renders, including gated ones

    report = health.Report()
    original = urllib.request.build_opener
    urllib.request.build_opener = lambda *a, **k: FakeOpener()
    try:
        health.check_admin_ui(report, "http://example")
    finally:
        urllib.request.build_opener = original

    out = capsys.readouterr().out
    assert "auth gate NOT active" in out
    assert report.failed >= 1


def test_missing_shared_tools_dir_is_a_failure(health, tmp_path, capsys):
    report = health.Report()
    (tmp_path / "empty").mkdir()
    health.check_shared_dir(report, tmp_path / "empty")
    assert "no tools/ subdirectory" in capsys.readouterr().out
    assert report.failed == 1


def test_shared_dir_check_is_independent_of_the_ui(health, tmp_path, capsys):
    """A filesystem problem must still be reported when the UI is down."""
    report = health.Report()
    (tmp_path / "empty").mkdir()
    health.check_admin_ui(report, "http://127.0.0.1:1")
    health.check_shared_dir(report, tmp_path / "empty")
    out = capsys.readouterr().out
    assert "is the process running?" in out
    assert "no tools/ subdirectory" in out


class _Group(ExceptionGroup):
    pass


def test_root_cause_unwraps_exception_groups(health):
    """AnyIO reports connection failures as a TaskGroup whose str() is
    'unhandled errors in a TaskGroup' — useless in an alert."""

    inner = ConnectionRefusedError("[Errno 111] Connection refused")
    grouped = _Group("unhandled errors in a TaskGroup", [inner])
    assert "Connection refused" in health._root_cause(grouped)


def test_root_cause_handles_chained_causes(health):
    try:
        try:
            raise ValueError("root problem")
        except ValueError as exc:
            raise RuntimeError("wrapper") from exc
    except RuntimeError as exc:
        assert health._root_cause(exc) == "root problem"


def test_root_cause_does_not_loop_forever(health):
    looped = RuntimeError("x")
    looped.__cause__ = looped
    assert health._root_cause(looped) == "x"
