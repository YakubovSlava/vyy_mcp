"""Reading the group store must never take the MCP server down.

`groups._load` runs inside the server's 5-second sync loop, so a raise here
doesn't just fail one page — it stops every tool from being registered and
the whole server goes dark for every connected agent. These tests pin that
a damaged `groups.json` degrades to "no groups" instead.
"""
import json

import pytest

from jupyter_mcp_tools.common import groups
from jupyter_mcp_tools.common.groups import Group, list_groups


@pytest.fixture
def store(tmp_path):
    return tmp_path


def test_missing_file_means_no_groups(store):
    assert list_groups(store) == []


def test_valid_store_roundtrips(store):
    groups.create_group(store, "networking", "Net things")
    assert list_groups(store) == [Group(name="networking", description="Net things")]


@pytest.mark.parametrize(
    "payload",
    [
        '["networking"]',  # valid JSON, wrong shape -- a hand edit
        '{"networking": "just a string"}',  # entry not an object
        "not json at all",
        "",
        "null",
    ],
)
def test_damaged_store_does_not_raise(store, payload):
    (store / "groups.json").write_text(payload)
    # Must not raise: this is called from the sync loop.
    assert list_groups(store) == []


def test_damaged_store_still_allows_new_groups(store):
    """Recovery path: a corrupted file shouldn't wedge the Группы tab."""
    (store / "groups.json").write_text('["broken"]')
    groups.create_group(store, "ops", "Ops things")
    assert [g.name for g in list_groups(store)] == ["ops"]


def test_sync_tools_survives_a_damaged_group_store(store):
    """The end-to-end regression: a broken groups.json used to abort
    `sync_tools` with AttributeError and register nothing."""
    from jupyter_mcp_tools.mcp_server import server as srv

    (store / "tools" / "echo").mkdir(parents=True)
    (store / "tools" / "echo" / "tool.py").write_text(
        'def echo(text: str) -> str:\n    """Echoes."""\n    return text\n'
    )
    (store / "groups.json").write_text('["networking"]')

    app = srv.build_app(store)
    srv.sync_tools(app, store)

    registered = {t.name for t in app._tool_manager.list_tools()}
    # Skipping groups means the skill registers standalone -- degraded, but
    # the server is still serving.
    assert "echo" in registered
